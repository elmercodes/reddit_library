"""Chat engine: runs only through the Claude Code subscription (app/subscription.py), never the API.

Chats are temporary: the browser holds the live conversation and sends it with each message, so
nothing is written to disk unless you press Save (app/saved.py).

What Claude sees each turn:
- system prompt: chat instructions + the library map for this tab
- a fresh chat: the full text-only transcript, then the new message
- a continued saved chat: the saved summary + the messages from (covers - 6) on, then the new message
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from claude_agent_sdk import tool

from lib import config as C
from lib.store import read_json

from . import subscription as sub

SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- models ------------------------------------------------------------------

def chat_models() -> list[dict]:
    return C.config()["chat_models"]


def default_model() -> str:
    models = chat_models()
    return next((m["id"] for m in models if m.get("default")), models[0]["id"])


def model_entry(model_id: str) -> dict:
    m = next((m for m in chat_models() if m["id"] == model_id), None)
    if not m:
        raise ValueError(f"{model_id} is not in config.yaml chat_models")
    return m


def chat_cfg() -> dict:
    return C.config()["chat"]


# --- library access (also the tools) --------------------------------------------

def built_books() -> list[str]:
    return sorted(p.parent.name for p in C.BOOKS.glob("*/index.md") if p.parent.name != "master")


def _front_matter(path) -> dict:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        import yaml
        return yaml.safe_load(text.split("---", 2)[1]) or {}
    return {}


def book_map(book: str) -> str:
    """Compact map of one book for the system prompt."""
    from lib.bookstats import chapter_stats, load_threads
    front = _front_matter(C.BOOKS / book / "index.md")
    plan = read_json(C.BOOKS / book / "plan.json")
    stats = chapter_stats(plan, load_threads(book)) if plan else {}
    lines = [f"## Book: r/{book} (book id: {book})"]
    for ch in front.get("chapters", []):
        saved = read_json(C.BOOKS / book / "chapters" / f"{ch['slug']}.json") or {}
        s = stats.get(ch["slug"], {})
        lines.append(
            f"- {ch['title']} (chapter: {ch['slug']}; link: #/book/{book}/{ch['slug']}; "
            f"{s.get('threads', '?')} threads, heat {s.get('heat', 0):.0f}, "
            f"{s.get('comments_per_thread', 0):.0f} comments/thread)\n"
            f"  What belongs here: {ch['description']}\n"
            f"  TL;DR: {saved.get('tldr', '(not written)')}")
    return "\n".join(lines)


def read_chapter(book: str, chapter: str) -> str:
    if not (SAFE.match(book) and SAFE.match(chapter)):
        return "Invalid book or chapter id."
    path = C.BOOKS / book / "chapters" / f"{chapter}.md"
    if not path.exists():
        return f"No chapter '{chapter}' in book '{book}'. Check the library map for valid slugs."
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^\s*- !\[\]\(.*$\n?", "", text, flags=re.M)      # drop thumbnail lines
    # Inline citations "([T1](url), [S2](url))" -> "[T1, S2]"; each URL stays once in the Threads list.
    text = re.sub(r"\(((?:\[[TS]\d+\]\([^)]+\)(?:,\s*)?)+)\)",
                  lambda m: "[" + ", ".join(re.findall(r"\[([TS]\d+)\]", m.group(1))) + "]", text)
    return (f"App link for this chapter: #/book/{book}/{chapter}\n"
            f"Citations like [T1] refer to the Threads list at the end, which has each thread's Reddit URL.\n\n{text}")


def read_thread(thread_id: str) -> str:
    if not SAFE.match(thread_id):
        return "Invalid thread id."
    d = read_json(C.THREADS / f"{thread_id}.json")
    if not d:
        return f"No thread '{thread_id}' in the library."
    slim = lambda items, k: [{k: i[k], "support": {x: i["support"][x] for x in ("commenters", "points")}}
                             for i in items]
    out = {k: d.get(k) for k in ("id", "title", "url", "subreddit", "date", "upvotes", "comments",
                                 "flair", "topics", "summary")}
    out["consensus"] = slim(d["consensus"], "point")
    out["notable_points"] = slim(d["notable_points"][:6], "point")
    out["splits"] = [{"issue": s["issue"], "side_a": s["side_a"], "side_b": s["side_b"],
                      "side_a_support": s["side_a_support"], "side_b_support": s["side_b_support"]}
                     for s in d["disagreements"]]
    out["design_lessons"] = slim(d["design_lessons"], "lesson")
    out["business_lessons"] = slim(d["business_lessons"], "lesson")
    out["content_ideas"] = [{"type": i["type"], "hook": i["hook"]} for i in d["content_ideas"]]
    if d.get("image_notes"):
        out["image_notes"] = {"what_is_asked": d["image_notes"]["what_is_asked"],
                              "images": [i["description"] for i in d["image_notes"]["images"]]}
    return json.dumps(out, ensure_ascii=False)


def read_idea(idea_id: str) -> str:
    if not SAFE.match(idea_id):
        return "Invalid idea id."
    board = read_json(C.BOARD) or []
    if not board:
        return "The Ideas Board is empty (it is built in a later phase), so there is no idea to read yet."
    idea = next((i for i in board if str(i.get("id")) == idea_id), None)
    if not idea:
        return f"No idea '{idea_id}' on the Ideas Board."
    return json.dumps(idea, ensure_ascii=False)


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    try:
        if name == "read_chapter":
            return read_chapter(str(args["book"]), str(args["chapter"])), False
        if name == "read_thread":
            return read_thread(str(args["id"])), False
        if name == "read_idea":
            return read_idea(str(args["id"])), False
        return f"Unknown tool {name}", True
    except (KeyError, TypeError) as e:
        return f"Invalid tool input: {e}", True


def _result(name: str, args: dict) -> dict:
    text, is_error = run_tool(name, args)
    return {"content": [{"type": "text", "text": text}], **({"is_error": True} if is_error else {})}


def _schema(props: dict) -> dict:
    return {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
            "required": list(props), "additionalProperties": False}


@tool("read_chapter",
      "Read one chapter of a book in full: TL;DR, stats, consensus, splits with support, design and business "
      "lessons, content ideas, and its threads with Reddit links.",
      _schema({"book": "Book id, e.g. homemadeTCGs", "chapter": "Chapter slug from the library map"}))
async def read_chapter_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _result("read_chapter", args)


@tool("read_thread",
      "Read one Reddit thread's structured notes: summary, consensus, splits, lessons, content ideas, "
      "support counts, image notes and its Reddit URL.",
      _schema({"id": "Reddit post id, e.g. 1qv7ztx"}))
async def read_thread_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _result("read_thread", args)


@tool("read_idea",
      "Read one entry from the Ideas Board (content ideas de-duplicated across books) by its id.",
      _schema({"id": "Idea id from the Ideas Board"}))
async def read_idea_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _result("read_idea", args)


LIBRARY_TOOLS = [read_chapter_tool, read_thread_tool, read_idea_tool]


def tool_label(name: str, args: dict) -> str:
    """Human label for a tool call, e.g. the chapter title being read."""
    from .bookdata import chapter_title
    try:
        if name == "read_chapter" and SAFE.match(str(args.get("book", ""))) and SAFE.match(str(args.get("chapter", ""))):
            return chapter_title(args["book"], args["chapter"])
        if name == "read_thread" and SAFE.match(str(args.get("id", ""))):
            d = read_json(C.THREADS / f"{args['id']}.json")
            return d["title"] if d else f"thread {args['id']}"
        if name == "read_idea":
            return f"idea {args.get('id', '')}".strip()
    except Exception:  # noqa: BLE001
        pass
    return name


_CH_LINK = re.compile(r"\(#/book/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)\)")
_RD_LINK = re.compile(r"\[([^\]]+)\]\((https://(?:www\.|old\.)?reddit\.com/[^)\s]+)\)")


def sources_for(answer: str, tools_used: list[dict]) -> list[dict]:
    """Chapters and threads the answer read or linked, for the source chips under it."""
    from .bookdata import chapter_title
    out, seen = [], set()

    def add(key, item):
        if key not in seen:
            seen.add(key)
            out.append(item)

    chapters = [(t["input"].get("book"), t["input"].get("chapter")) for t in tools_used if t["name"] == "read_chapter"]
    chapters += _CH_LINK.findall(answer)
    for book, slug in chapters:
        if book and slug and SAFE.match(book) and SAFE.match(slug) and (C.BOOKS / book / "chapters" / f"{slug}.md").exists():
            add(("ch", book, slug), {"type": "chapter", "book": book, "slug": slug, "title": chapter_title(book, slug)})
    for t in tools_used:
        if t["name"] == "read_thread" and SAFE.match(str(t["input"].get("id", ""))):
            d = read_json(C.THREADS / f"{t['input']['id']}.json")
            if d:
                add(("th", d["url"]), {"type": "thread", "title": d["title"], "url": d["url"]})
    for title, url in _RD_LINK.findall(answer):
        add(("th", url), {"type": "thread", "title": title, "url": url})
    return out


# --- tabs ----------------------------------------------------------------------

def tab_kind(tab: str) -> tuple[str, str]:
    kind, _, key = tab.partition(":")
    if kind not in ("book", "game") or not key or not SAFE.match(key):
        raise ValueError(f"Chat is not available for tab {tab!r}")
    return kind, key


def system_prompt(tab: str) -> str:
    kind, key = tab_kind(tab)
    instructions = (C.PROMPTS / "chat.md").read_text(encoding="utf-8")
    if kind == "book":
        if key not in built_books():
            raise ValueError(f"Book {key} is not built yet")
        context = ("This tab is the book below. Answer from this book.\n\n" + book_map(key))
    else:
        game_md = (C.GAMES / key / "game.md")
        game_text = game_md.read_text(encoding="utf-8") if game_md.exists() else "(empty)"
        maps = "\n\n".join(book_map(b) for b in built_books()) or "(no books built yet)"
        context = (
            "This tab is one of the designer's own games. Answer specifically for this game: connect what "
            "the communities say to its actual rules, cards and plans, and say what would likely break, "
            "confuse players, or land well. Read the relevant chapters before advising.\n\n"
            f"# The designer's game ({key})\n\n{game_text}\n\n# Library\n\n{maps}")
    return instructions + "\n\n" + context


# --- the turn -------------------------------------------------------------------

def context_messages(history: list[dict], saved: dict | None) -> tuple[str | None, list[dict]]:
    """(summary or None, messages to replay). Continued chats replay the last N saved messages
    plus everything added since; fresh chats replay everything."""
    msgs = [m for m in history if m.get("text") and m.get("role") in ("user", "assistant")]
    if not saved or not saved.get("summary"):
        return None, msgs
    keep = chat_cfg().get("continue_recent_messages", 6)
    start = max(0, int(saved.get("covers", len(msgs))) - keep)
    return saved["summary"], msgs[start:]


def build_prompt(text: str, summary: str | None, replay: list[dict]) -> str:
    parts = []
    if summary:
        parts.append(f"<earlier_conversation_summary>\n{summary}\n</earlier_conversation_summary>")
    if replay:
        turns = "\n\n".join(f"<{m['role']}>\n{m['text']}\n</{m['role']}>" for m in replay)
        parts.append(f"<conversation_so_far>\n{turns}\n</conversation_so_far>")
    if parts:
        parts.append("Continue the conversation above. The designer's new message:")
    parts.append(text)
    return "\n\n".join(parts)


async def send(tab: str, text: str, model_id: str | None, history: list[dict],
               saved: dict | None = None) -> AsyncIterator[dict]:
    """Async generator of events: {"type": "text"|"tool"|"notice"|"done"|"error", ...}."""
    model_id = model_id or default_model()
    entry = model_entry(model_id)
    summary, replay = context_messages(history, saved)
    prompt = build_prompt(text, summary, replay)
    options = sub.build_options(system_prompt(tab), model_id, entry.get("effort"), tools=LIBRARY_TOOLS,
                                max_turns=chat_cfg().get("max_tool_rounds", 8) + 1)
    answer_parts: list[str] = []
    tools_used: list[dict] = []
    usage, subtype, init = {}, "success", {}
    try:
        async for ev in sub.run(prompt, options):
            if ev["type"] == "init":
                init = ev
            elif ev["type"] == "text":
                answer_parts.append(ev["delta"])
                yield {"type": "text", "delta": ev["delta"]}
            elif ev["type"] == "tool":
                label = tool_label(ev["name"], ev["input"])
                tools_used.append({"name": ev["name"], "input": ev["input"], "label": label})
                yield {"type": "tool", "name": ev["name"], "input": ev["input"], "label": label}
                if answer_parts and not answer_parts[-1].endswith("\n"):
                    answer_parts.append("\n\n")
                    yield {"type": "text", "delta": "\n\n"}
            elif ev["type"] == "notice":
                yield ev
            elif ev["type"] == "result":
                usage, subtype = ev["usage"], ev["subtype"]
    except sub.SubscriptionError as e:
        yield {"type": "error", "kind": e.kind, "message": str(e)}
        return

    answer = "".join(answer_parts).strip()
    if subtype == "error_max_turns":
        answer += "\n\n*(Stopped after the maximum number of library reads.)*"
    msg = {"role": "assistant", "text": answer, "ts": now(), "model": model_id, "model_label": entry["label"],
           "via": "subscription", "usage": usage, "tokens": sub.total_tokens(usage),
           "tools_used": tools_used, "sources": sources_for(answer, tools_used)}
    yield {"type": "done", "message": msg, "credential": init.get("api_key_source"), "tools": init.get("tools")}
