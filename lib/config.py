"""Loads config.yaml and .env. Paths are resolved relative to the project root."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
RAW = DATA / "raw"
THREADS = DATA / "threads"
IMAGES = DATA / "images"
LEDGER = DATA / "ledger.json"
COSTS = DATA / "costs.csv"
BOOKS = ROOT / "books"
MASTER = BOOKS / "master"
IDEAS = ROOT / "ideas"
BOARD = IDEAS / "board.json"
GAMES = ROOT / "games"
INBOX = ROOT / "inbox"
CHATS = ROOT / "chats"
PROMPTS = ROOT / "lib" / "prompts"

SECRET_NAMES = ("APIFY_TOKEN", "ANTHROPIC_API_KEY")


@lru_cache(maxsize=1)
def config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def reload() -> dict:
    config.cache_clear()
    return config()


def secret(name: str) -> str | None:
    """Return a secret from .env / environment. Never print the result."""
    load_dotenv(ROOT / ".env", override=False)
    value = os.environ.get(name, "").strip()
    return value or None


def active_collector() -> tuple[str, dict]:
    cfg = config()
    name = cfg["collector"]
    return name, cfg["collectors"][name]


def price(model: str) -> dict:
    prices = config()["prices"]
    if model not in prices:
        raise KeyError(f"No price for model {model!r} in config.yaml `prices`.")
    return prices[model]
