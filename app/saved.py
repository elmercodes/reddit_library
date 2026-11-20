"""Saved chats: chats/saved/<tab-id>/<YYYY-MM-DD>-<title-slug>.json.

Only written when you press Save. The title and summary are written at save time through the
Claude Code subscription (never the API). Saving again after Continue updates the same file.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from lib import config as C
from lib.store import read_json, write_json

from . import chat
from . import subscription as sub

SAFE = re.compile(r"^[A-Za-z0-9_-]+$")

SUMMARY_SYSTEM = (
    "You write titles and summaries of saved conversations between an independent card game designer and "
    "the research assistant in their private Reddit-research library app. Reply in exactly this format:\n"
    "Title: <3 to 8 words, no quotes>\n"
    "Summary:\n<a compact summary, at most 250 words, that lets the conversation continue without the full "
    "transcript: what was asked, decisions and conclusions, details of the designer's game that came up, "
    "and every chapter link (#/book/...) or Reddit link that was cited>")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_date(iso: str) -> str:
    """YYYY-MM-DD in your local time zone, for file names."""
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d")


def tab_dir(tab: str):
    kind, key = chat.tab_kind(tab)
    return C.CHATS / "saved" / f"{kind}-{key}"


def _path(tab: str, saved_id: str):
    if not SAFE.match(saved_id or ""):
        raise ValueError(f"Invalid saved chat id {saved_id!r}")
    return tab_dir(tab) / f"{saved_id}.json"


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-") or "chat"


def _free_id(tab: str, date: str, title: str, current: str | None = None) -> str:
    base = f"{date}-{slugify(title)}"
    cand, n = base, 2
    while (tab_dir(tab) / f"{cand}.json").exists() and cand != current:
        cand, n = f"{base}-{n}", n + 1
    return cand


def _meta(saved_id: str, d: dict) -> dict:
    return {"id": saved_id, "title": d["title"], "created_at": d["created_at"], "updated_at": d["updated_at"],
            "model": d.get("model"), "model_label": d.get("model_label"), "messages": len(d["messages"])}


def list_saved(tab: str) -> list[dict]:
    out = []
    for f in tab_dir(tab).glob("*.json"):
        d = read_json(f)
        if d and d.get("messages") is not None:
            out.append(_meta(f.stem, d))
    return sorted(out, key=lambda m: m["updated_at"], reverse=True)


def get(tab: str, saved_id: str) -> dict | None:
    d = read_json(_path(tab, saved_id))
    return {**d, "id": saved_id} if d else None


def transcript(messages: list[dict]) -> str:
    return "\n\n".join(f"{m['role'].upper()}: {m['text']}" for m in messages if m.get("text"))


def parse_summary(text: str, messages: list[dict]) -> tuple[str, str]:
    m = re.search(r"^\s*Title:\s*(.+)$", text, re.M)
    title = m.group(1).strip().strip('"*') if m else ""
    s = re.search(r"Summary:\s*(.*)\Z", text, re.S)
    summary = (s.group(1) if s else text).strip()
    if not title:
        first = next((x["text"] for x in messages if x["role"] == "user"), "Chat")
        title = first.strip().splitlines()[0][:60]
    return title[:80], summary


async def summarize(messages: list[dict]) -> tuple[str, str, dict]:
    model = chat.chat_cfg().get("summary_model", "haiku")
    text, usage = await sub.complete("Conversation to title and summarize:\n\n" + transcript(messages),
                                     SUMMARY_SYSTEM, model, effort="low")
    title, summary = parse_summary(text, messages)
    return title, summary, usage


def _clean(messages: list[dict]) -> list[dict]:
    keys = ("role", "text", "ts", "model", "model_label", "via", "usage", "tokens", "tools_used", "sources")
    out = []
    for m in messages:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("text"), str):
            out.append({k: m[k] for k in keys if k in m})
    return out


async def save(tab: str, messages: list[dict], saved_id: str | None = None) -> dict:
    """Create a saved chat, or update `saved_id` with the full transcript and a fresh summary.
    Raises SubscriptionError (nothing written) if the summary can't be made."""
    messages = _clean(messages)
    if not messages:
        raise ValueError("Nothing to save yet")
    existing = get(tab, saved_id) if saved_id else None
    if saved_id and not existing:
        raise ValueError("That saved chat no longer exists")
    title, summary, usage = await summarize(messages)
    last_model = next((m for m in reversed(messages) if m["role"] == "assistant"), {})
    stamp = now()
    data = {
        "version": 1, "tab": tab,
        "title": existing["title"] if existing else title,
        "created_at": existing["created_at"] if existing else stamp,
        "updated_at": stamp,
        "model": last_model.get("model"), "model_label": last_model.get("model_label"),
        "summary": summary, "summary_usage": usage,
        "covers": len(messages),
        "messages": messages,
        "tokens_total": sum(m.get("tokens", 0) for m in messages),
    }
    if not existing:
        saved_id = _free_id(tab, local_date(stamp), data["title"])
    write_json(_path(tab, saved_id), data)
    return {**_meta(saved_id, data), "covers": data["covers"], "summary": summary}


def rename(tab: str, saved_id: str, title: str) -> dict:
    title = title.strip()[:80]
    if not title:
        raise ValueError("Give the chat a title")
    d = get(tab, saved_id)
    if not d:
        raise ValueError("That saved chat no longer exists")
    d.pop("id", None)
    d["title"] = title
    new_id = _free_id(tab, local_date(d["created_at"]), title, current=saved_id)
    write_json(_path(tab, new_id), d)
    if new_id != saved_id:
        _path(tab, saved_id).unlink()
    return _meta(new_id, d)


def delete(tab: str, saved_id: str) -> None:
    p = _path(tab, saved_id)
    if not p.exists():
        raise ValueError("That saved chat no longer exists")
    p.unlink()


# --- one-time migration of the old one-chat-per-tab files ---------------------------------

async def migrate_old() -> list[str]:
    """chats/<kind>-<key>.json (old format) -> a saved chat. Removes the old file after saving."""
    done = []
    for f in sorted(C.CHATS.glob("*.json")):
        d = read_json(f)
        kind, _, key = f.stem.partition("-")
        if not d or "messages" not in d or kind not in ("book", "game"):
            continue
        tab = f"{kind}:{key}"
        msgs = []
        for m in d["messages"]:
            m = dict(m)
            if m["role"] == "assistant":
                m["tokens"] = sub.total_tokens(m.get("usage") or {})
                m.pop("cost_usd", None)
            msgs.append(m)
        meta = await save(tab, msgs)
        saved_path = _path(tab, meta["id"])
        created = d["messages"][0].get("ts") if d["messages"] else None
        if created:          # keep the conversation's real start date
            data = read_json(saved_path)
            data["created_at"] = created
            write_json(saved_path, data)
        f.unlink()
        done.append(f"{f.name} -> {saved_path.relative_to(C.ROOT) if saved_path.is_relative_to(C.ROOT) else saved_path}")
    return done
