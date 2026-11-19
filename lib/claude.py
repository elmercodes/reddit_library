"""Thin wrapper over the Anthropic SDK: one place for models, effort, fallbacks, and cost.

Every call returns (result, cost_usd, usage_dict). Costs come from response.usage and the
prices in config.yaml, so they match what Anthropic bills.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

import anthropic

from . import config as C
from .costs import cost_from_usage

# Server-side refusal fallback for Opus/Sonnet 5.5 (not available on Haiku or in batches).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


class ClaudeError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def client() -> anthropic.Anthropic:
    key = C.secret("ANTHROPIC_API_KEY")
    if not key:
        raise ClaudeError("ANTHROPIC_API_KEY is not set in .env")
    return anthropic.Anthropic(api_key=key, max_retries=4)


def model_for(role: str) -> str:
    return C.config()["models"][role]


def effort_for(role: str) -> str | None:
    return C.config().get("effort", {}).get(role)


def usage_dict(usage) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: int(getattr(usage, k, 0) or 0) for k in keys}


@dataclass
class Spend:
    """Running total for one pipeline step."""
    usd: float = 0.0
    calls: int = 0
    tokens: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0,
                                                  "cache_creation_input_tokens": 0,
                                                  "cache_read_input_tokens": 0})

    def add(self, usd: float, usage: dict) -> None:
        self.usd += usd
        self.calls += 1
        for k, v in usage.items():
            self.tokens[k] = self.tokens.get(k, 0) + v

    def units(self) -> str:
        t = self.tokens
        return (f"{self.calls} calls, {t['input_tokens'] + t['cache_creation_input_tokens'] + t['cache_read_input_tokens']:,} in"
                f" / {t['output_tokens']:,} out tokens")


def build_request(role: str, messages: list, system=None, schema: dict | None = None,
                  max_tokens: int = 16000, model: str | None = None) -> dict:
    model = model or model_for(role)
    params: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        params["system"] = system
    output_config: dict = {}
    effort = effort_for(role)
    if effort:
        output_config["effort"] = effort
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    if output_config:
        params["output_config"] = output_config
    return params


def call(role: str, messages: list, system=None, schema: dict | None = None,
         max_tokens: int = 16000, model: str | None = None):
    """One Messages API call. Returns (text_or_parsed_json, cost_usd, usage_dict)."""
    params = build_request(role, messages, system, schema, max_tokens, model)
    model = params["model"]
    if model in FALLBACK_MODELS:
        resp = client().beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
    else:
        resp = client().messages.create(**params)

    usage = usage_dict(resp.usage)
    # A fallback may have served the request; price it by the model that answered.
    cost = cost_from_usage(resp.model if resp.model in C.config()["prices"] else model, usage)

    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        raise ClaudeError(f"Refused ({getattr(details, 'category', None)}): {getattr(details, 'explanation', '')}")
    if resp.stop_reason == "max_tokens":
        raise ClaudeError(f"Hit max_tokens={max_tokens}; output truncated.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    result = json.loads(text) if schema else text
    return result, cost, usage
