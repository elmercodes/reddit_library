"""The app's only route to Claude: the Claude Agent SDK, logged in with the Claude Code subscription.

Chat must never bill the Anthropic API. Layers of protection:
- This module (and all of app/) never imports `anthropic`, `lib.claude` or `lib.costs`
  (tests/test_chat.py fails if it does).
- `scrub_env()` removes API credentials from this process's environment; the SDK builds the CLI's
  environment from os.environ, so a key that isn't here can't reach the CLI.
- The CLI runs with no setting sources (no apiKeyHelper, hooks, plugins), no built-in tools,
  only the in-process library tools, no other MCP servers, and no session files on disk.
- Every run reads the CLI's init message and aborts unless `apiKeySource` is "none" (claude.ai
  login) and the tool list is exactly the library tools.
- Usage-limit errors are reported as such; nothing falls back to the API.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)

from lib import config as C

# Anything that could make the CLI use an API key or another provider instead of the subscription.
FORBIDDEN_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
)
# Built-in Claude Code tools, removed by name as a second guard on top of tools=[].
BUILTIN_TOOLS = (
    "Bash", "BashOutput", "KillShell", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Glob", "Grep", "LS", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "Skill",
    "SlashCommand", "ExitPlanMode", "ListMcpResourcesTool", "ReadMcpResourceTool",
)
MCP_SERVER = "library"

LOGIN_HELP = "Run `claude` in a terminal and log in with your Claude account (/login), then try again."


class SubscriptionError(RuntimeError):
    """kind: login | usage_limit | guard | error."""
    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def scrub_env() -> None:
    for k in FORBIDDEN_ENV:
        os.environ.pop(k, None)


def cli_path() -> str | None:
    return shutil.which("claude")


# --- login check ----------------------------------------------------------------

_status: dict[str, Any] = {"at": 0.0, "value": None}


def login_status(force: bool = False) -> dict:
    """{"ok", "message", "plan"} from `claude auth status`, cached for 60 s."""
    if not force and _status["value"] and time.time() - _status["at"] < 60:
        return _status["value"]
    scrub_env()
    value = _check_login()
    _status.update(at=time.time(), value=value)
    return value


def _check_login() -> dict:
    cli = cli_path()
    if not cli:
        return {"ok": False, "plan": None,
                "message": "Claude Code isn't installed (no `claude` on PATH). Install it, run `claude` and log in."}
    try:
        out = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ))
        info = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"ok": False, "plan": None, "message": f"Couldn't check the Claude Code login ({e}). {LOGIN_HELP}"}
    if not info.get("loggedIn"):
        return {"ok": False, "plan": None, "message": f"You're not logged in to Claude Code. {LOGIN_HELP}"}
    if info.get("authMethod") != "claude.ai":
        return {"ok": False, "plan": None,
                "message": "Claude Code is logged in with an API key or Console account, not your subscription. "
                           "Run `claude`, then /logout and /login with your Claude account."}
    return {"ok": True, "plan": info.get("subscriptionType"), "message": None}


def clear_status_cache() -> None:
    _status.update(at=0.0, value=None)


# --- options --------------------------------------------------------------------

def tool_names(tools: list) -> list[str]:
    return [f"mcp__{MCP_SERVER}__{t.name}" for t in tools]


def build_options(system_prompt: str, model: str, effort: str | None = None, tools: list | None = None,
                  max_turns: int = 1, stream: bool = True) -> ClaudeAgentOptions:
    tools = tools or []
    servers = {MCP_SERVER: create_sdk_mcp_server(name=MCP_SERVER, version="1.0.0", tools=tools)} if tools else {}
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,                        # alias: opus / sonnet / haiku
        effort=effort,
        tools=[],                           # no built-in tools at all
        disallowed_tools=list(BUILTIN_TOOLS),
        allowed_tools=tool_names(tools),
        permission_mode="dontAsk",          # anything not allowed above is denied
        mcp_servers=servers,
        strict_mcp_config=True,             # ignore every other MCP config
        setting_sources=[],                 # no user/project settings, hooks, plugins, apiKeyHelper
        extra_args={"no-session-persistence": None},   # no transcript written to ~/.claude/projects
        include_partial_messages=stream,
        max_turns=max_turns,
        cli_path=cli_path(),
        cwd=str(C.ROOT),
    )


# --- running ----------------------------------------------------------------------

def _when(ts: int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).astimezone().strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def usage_limit_message(resets_at: int | None = None) -> str:
    when = f" It resets {_when(resets_at)}." if resets_at else ""
    return f"You've hit your Claude plan's usage limit.{when} Chat never switches to the API, so try again after the reset."


def _usage(u: dict | None) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: int((u or {}).get(k) or 0) for k in keys}


def total_tokens(u: dict) -> int:
    return sum(_usage(u).values())


def _make_client(options: ClaudeAgentOptions):
    return ClaudeSDKClient(options=options)


async def run(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[dict]:
    """Run one turn. Yields {"type": "init"|"text"|"tool"|"notice"|"result", ...}.
    Raises SubscriptionError for login problems, usage limits, guard failures and errors."""
    scrub_env()
    status = login_status()
    if not status["ok"]:
        raise SubscriptionError(status["message"], "login")
    expected_tools = set(options.allowed_tools)
    last_reset = None
    try:
        async with _make_client(options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, SystemMessage) and msg.subtype == "init":
                    source = msg.data.get("apiKeySource")
                    tools = set(msg.data.get("tools") or [])
                    if source != "none":
                        await client.interrupt()
                        raise SubscriptionError(
                            f"Stopped: Claude Code reported credential source {source!r} instead of your "
                            "subscription login. Chat only runs on the subscription.", "guard")
                    if not tools <= expected_tools:
                        await client.interrupt()
                        raise SubscriptionError(
                            f"Stopped: unexpected tools offered to the model: {sorted(tools - expected_tools)}", "guard")
                    yield {"type": "init", "api_key_source": source, "tools": sorted(tools),
                           "model": msg.data.get("model")}
                elif isinstance(msg, StreamEvent):
                    ev = msg.event or {}
                    delta = ev.get("delta") or {}
                    if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta" \
                            and msg.parent_tool_use_id is None:
                        yield {"type": "text", "delta": delta.get("text", "")}
                elif isinstance(msg, RateLimitEvent):
                    info = msg.rate_limit_info
                    last_reset = info.resets_at or last_reset
                    if info.status == "rejected":
                        raise SubscriptionError(usage_limit_message(info.resets_at), "usage_limit")
                    if info.status == "allowed_warning":
                        pct = f" ({round(info.utilization * 100)}% used)" if info.utilization else ""
                        yield {"type": "notice", "message": f"You're close to your plan's usage limit{pct}."}
                elif isinstance(msg, AssistantMessage):
                    if msg.error:
                        _raise_for_error(msg.error, msg, last_reset)
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            yield {"type": "tool", "name": block.name.removeprefix(f"mcp__{MCP_SERVER}__"),
                                   "input": block.input if isinstance(block.input, dict) else {}}
                        elif isinstance(block, TextBlock) and not options.include_partial_messages:
                            yield {"type": "text", "delta": block.text}
                elif isinstance(msg, ResultMessage):
                    if msg.api_error_status == 429:
                        raise SubscriptionError(usage_limit_message(last_reset), "usage_limit")
                    if msg.is_error and msg.subtype != "error_max_turns":
                        detail = "; ".join(msg.errors or []) or msg.result or msg.subtype
                        raise SubscriptionError(f"Claude Code returned an error: {detail}")
                    # total_cost_usd / costUSD are client-side API-price estimates: ignored on purpose.
                    yield {"type": "result", "usage": _usage(msg.usage), "subtype": msg.subtype,
                           "stop_reason": msg.stop_reason, "num_turns": msg.num_turns}
    except SubscriptionError:
        raise
    except ClaudeSDKError as e:
        text = str(e)
        if "usage limit" in text.lower() or "rate_limit" in text.lower():
            raise SubscriptionError(usage_limit_message(last_reset), "usage_limit") from e
        if "login" in text.lower() or "auth" in text.lower():
            clear_status_cache()
            raise SubscriptionError(f"{text}. {LOGIN_HELP}", "login") from e
        raise SubscriptionError(f"Claude Code error: {text}") from e


def _raise_for_error(error: str, msg: AssistantMessage, last_reset: int | None) -> None:
    text = " ".join(b.text for b in msg.content if isinstance(b, TextBlock))
    if error == "rate_limit":
        raise SubscriptionError(usage_limit_message(last_reset), "usage_limit")
    if error in ("authentication_failed", "billing_error"):
        clear_status_cache()
        raise SubscriptionError(f"Claude Code couldn't use your subscription ({error}). {LOGIN_HELP}", "login")
    raise SubscriptionError(f"Claude Code error ({error}): {text}".strip())


# --- one-shot text (save-time summaries) --------------------------------------------

async def complete(prompt: str, system_prompt: str, model: str, effort: str | None = "low") -> tuple[str, dict]:
    """One no-tool turn through the subscription. Returns (text, usage)."""
    options = build_options(system_prompt, model, effort, tools=None, max_turns=1, stream=False)
    parts, usage = [], {}
    async for ev in run(prompt, options):
        if ev["type"] == "text":
            parts.append(ev["delta"])
        elif ev["type"] == "result":
            usage = ev["usage"]
    return "".join(parts).strip(), usage
