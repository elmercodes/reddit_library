"""Chat runs only on the Claude Code subscription. No test here touches the network."""

import ast
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)
from claude_agent_sdk.types import RateLimitInfo

from app import chat, saved
from app import subscription as sub

ROOT = Path(__file__).resolve().parent.parent
APP_MODULES = ["app.server", "app.chat", "app.saved", "app.subscription", "app.bookdata", "lib.bookstats"]


# --- chat can never reach the Anthropic API ---------------------------------------------

def test_app_import_graph_never_loads_the_anthropic_client():
    """Import every app module with `anthropic` blocked: any import of the API client fails."""
    code = ("import sys; sys.modules['anthropic'] = None\n"
            + "".join(f"import {m}\n" for m in APP_MODULES)
            + "assert 'lib.claude' not in sys.modules and 'lib.costs' not in sys.modules, "
              "[m for m in sys.modules if m in ('lib.claude', 'lib.costs')]\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_no_chat_source_file_mentions_the_api_client_or_costs():
    files = list((ROOT / "app").glob("*.py")) + [ROOT / "lib" / "bookstats.py"]
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                names = [f"{node.value.id}.{node.attr}"]
            else:
                continue
            for n in names:
                assert not (n == "anthropic" or n.startswith("anthropic.")), f"{f.name} imports {n}"
                assert n not in ("lib.claude", "lib.costs", "costs.log", "claude.call", "claude.client"), \
                    f"{f.name} uses {n}"


def test_scrub_env_removes_api_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://example.invalid")
    sub.scrub_env()
    for k in sub.FORBIDDEN_ENV:
        assert k not in os.environ


def test_options_lock_the_cli_down():
    o = sub.build_options("sys", "haiku", "low", tools=chat.LIBRARY_TOOLS, max_turns=9)
    assert o.model == "haiku" and o.effort == "low"
    assert o.tools == []
    assert sorted(o.allowed_tools) == ["mcp__library__read_chapter", "mcp__library__read_idea",
                                       "mcp__library__read_thread"]
    assert {"Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch"} <= set(o.disallowed_tools)
    assert o.permission_mode == "dontAsk"
    assert o.setting_sources == []
    assert o.strict_mcp_config is True
    assert "no-session-persistence" in o.extra_args and "bare" not in o.extra_args
    assert list(o.mcp_servers) == ["library"]
    assert not any(k.startswith("ANTHROPIC") for k in o.env)


def test_chat_models_are_aliases():
    assert [m["id"] for m in chat.chat_models()] == ["opus", "sonnet", "haiku"]


# --- context building -------------------------------------------------------------------

def _msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "text": f"m{i}"} for i in range(n)]


def test_fresh_chat_replays_everything():
    summary, replay = chat.context_messages(_msgs(10), None)
    assert summary is None and [m["text"] for m in replay] == [f"m{i}" for i in range(10)]


def test_continued_chat_gets_summary_plus_last_six_and_new_messages():
    saved_chat = {"summary": "S", "covers": 10}
    summary, replay = chat.context_messages(_msgs(10), saved_chat)
    assert summary == "S" and [m["text"] for m in replay] == [f"m{i}" for i in range(4, 10)]
    summary, replay = chat.context_messages(_msgs(14), saved_chat)      # 4 more since the save
    assert [m["text"] for m in replay] == [f"m{i}" for i in range(4, 14)]
    prompt = chat.build_prompt("next?", summary, replay)
    assert "<earlier_conversation_summary>\nS" in prompt and prompt.endswith("next?") and "m3" not in prompt


# --- the run loop, with a fake SDK client -------------------------------------------------

def _init(source="none", tools=None):
    tools = tools if tools is not None else sub.tool_names(chat.LIBRARY_TOOLS)
    return SystemMessage("init", {"apiKeySource": source, "tools": tools, "model": "claude-haiku-5-5"})


def _result(**kw):
    base = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s",
                usage={"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 5},
                total_cost_usd=0.01)
    base.update(kw)
    return ResultMessage(**base)


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.interrupted = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def query(self, prompt):
        self.prompt = prompt

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        for m in self.messages:
            yield m


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(sub, "login_status", lambda force=False: {"ok": True, "plan": "pro", "message": None})
    holder = {}

    def use(messages):
        holder["client"] = FakeClient(messages)
        monkeypatch.setattr(sub, "_make_client", lambda options: holder["client"])
        return holder
    return use


def _collect(gen):
    async def go():
        return [ev async for ev in gen]
    return asyncio.run(go())


def _send(history=None):
    tab = f"book:{chat.built_books()[0]}" if chat.built_books() else None
    if not tab:
        pytest.skip("no built book")
    return _collect(chat.send(tab, "hi", "haiku", history or []))


def test_normal_turn_streams_text_tools_and_token_usage(fake):
    fake([_init(),
          StreamEvent("u", "s", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Let me look."}}),
          AssistantMessage([ToolUseBlock("t1", "mcp__library__read_thread", {"id": "nope"})], "claude-haiku-5-5"),
          StreamEvent("u", "s", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Done."}}),
          _result()])
    evs = _send()
    assert [e["type"] for e in evs] == ["text", "tool", "text", "text", "done"]
    done = evs[-1]["message"]
    assert done["text"] == "Let me look.\n\nDone." and done["via"] == "subscription"
    assert done["tokens"] == 125 and "cost_usd" not in done
    assert evs[-1]["credential"] == "none"


def test_api_key_credential_aborts_the_run(fake):
    h = fake([_init(source="ANTHROPIC_API_KEY"), _result()])
    evs = _send()
    assert evs[-1]["type"] == "error" and evs[-1]["kind"] == "guard" and h["client"].interrupted


def test_unexpected_tool_aborts_the_run(fake):
    fake([_init(tools=["Bash", *sub.tool_names(chat.LIBRARY_TOOLS)]), _result()])
    assert _send()[-1]["kind"] == "guard"


@pytest.mark.parametrize("messages", [
    [_init(), RateLimitEvent(RateLimitInfo(status="rejected", resets_at=1_900_000_000), "u", "s")],
    [_init(), AssistantMessage([TextBlock("limit reached")], "m", error="rate_limit")],
    [_init(), _result(is_error=True, subtype="error_during_execution", api_error_status=429)],
])
def test_usage_limit_is_reported_not_rerouted(fake, messages):
    fake(messages)
    ev = _send()[-1]
    assert ev["type"] == "error" and ev["kind"] == "usage_limit" and "usage limit" in ev["message"]


def test_not_logged_in_tells_you_to_run_claude(monkeypatch):
    monkeypatch.setattr(sub, "login_status", lambda force=False: {"ok": False, "plan": None,
                                                                  "message": sub.LOGIN_HELP})
    ev = _send()[-1]
    assert ev["kind"] == "login" and "`claude`" in ev["message"]


# --- saved chats -------------------------------------------------------------------------

def test_saved_chat_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(saved.C, "CHATS", tmp_path)

    async def fake_summary(messages):
        return "Card layout basics", f"summary of {len(messages)}", {"output_tokens": 3}
    monkeypatch.setattr(saved, "summarize", fake_summary)
    tab = "book:homemadeTCGs"
    msgs = [{"role": "user", "text": "q"}, {"role": "assistant", "text": "a", "model": "haiku",
                                             "model_label": "Haiku (quick)", "tokens": 50}]
    meta = asyncio.run(saved.save(tab, msgs))
    assert meta["id"].endswith("-card-layout-basics") and meta["model"] == "haiku"
    path = tmp_path / "saved" / "book-homemadeTCGs" / f"{meta['id']}.json"
    assert path.exists()

    meta2 = asyncio.run(saved.save(tab, msgs + [{"role": "user", "text": "q2"}], meta["id"]))
    assert meta2["id"] == meta["id"] and meta2["messages"] == 3 and meta2["summary"] == "summary of 3"
    assert len(list(path.parent.glob("*.json"))) == 1

    renamed = saved.rename(tab, meta["id"], "My Rename")
    assert renamed["id"].endswith("-my-rename") and not path.exists()
    assert saved.list_saved(tab)[0]["title"] == "My Rename"
    meta3 = asyncio.run(saved.save(tab, msgs, renamed["id"]))
    assert meta3["title"] == "My Rename"                     # re-saving keeps your title

    saved.delete(tab, renamed["id"])
    assert saved.list_saved(tab) == []
    with pytest.raises(ValueError):
        saved.get(tab, "../x")


def test_parse_summary_falls_back_to_first_question():
    title, summary = saved.parse_summary("just text", [{"role": "user", "text": "How big are cards?\nmore"}])
    assert title == "How big are cards?" and summary == "just text"


# --- unchanged guards -------------------------------------------------------------------

def test_tab_validation():
    assert chat.tab_kind("book:homemadeTCGs") == ("book", "homemadeTCGs")
    for bad in ("master", "ideas", "book:../x", "game:"):
        with pytest.raises(ValueError):
            chat.tab_kind(bad)


def test_tools_reject_traversal_and_bad_input():
    assert chat.run_tool("read_chapter", {"book": "../etc", "chapter": "x"}) == ("Invalid book or chapter id.", False)
    assert chat.run_tool("read_thread", {})[1] is True
    assert "Invalid" in chat.read_idea("../x")
