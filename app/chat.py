"""Chat engine: one saved conversation per tab, tool use over the library, per-message cost.

History design (keeps the API's preserved-thinking check happy and the prompt cache warm):
- Earlier turns are stored and replayed as plain text only. Removing all thinking blocks from
  earlier turns is allowed; inside one answer's tool loop, the assistant content (thinking,
  text, tool_use) is passed back unchanged.
- The system prompt is [instructions, library map (cached)]; a summary of older turns, when one
  exists, is appended as a mid-conversation system message, never by editing earlier turns.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from lib import claude
from lib import config as C
from lib import costs
from lib.store import read_json, write_json

SAFE = re.compile(r"^[A-Za-z0-9_-]+$")

TOOLS = [
    {
        "name": "read_chapter",
        "description": "Read one chapter of a book in full: TL;DR, stats, consensus, splits with support, "
                       "design and business lessons, content ideas, and its threads with Reddit links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "book": {"type": "string", "description": "Book id, e.g. homemadeTCGs"},
                "chapter": {"type": "string", "description": "Chapter slug from the library map"},
            },
            "required": ["book", "chapter"],
            "additionalProperties": False,
        },
        "strict": True,
        "eager_input_streaming": True,
    },
    {
        "name": "read_thread",
        "description": "Read one Reddit thread's structured notes: summary, consensus, splits, lessons, "
                       "content ideas, support counts, image notes and its Reddit URL.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Reddit post id, e.g. 1qv7ztx"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "strict": True,
        "eager_input_streaming": True,
    },
]


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
    from pipeline.cluster import load_threads
    from pipeline.write import chapter_stats
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


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    try:
        if name == "read_chapter":
            return read_chapter(str(args["book"]), str(args["chapter"])), False
        if name == "read_thread":
            return read_thread(str(args["id"])), False
        return f"Unknown tool {name}", True
    except (KeyError, TypeError) as e:
        return f"Invalid tool input: {e}", True


def tool_label(name: str, args: dict) -> str:
    """Human label for a tool call, e.g. the chapter title being read."""
    from .bookdata import chapter_title
    try:
        if name == "read_chapter" and SAFE.match(str(args.get("book", ""))) and SAFE.match(str(args.get("chapter", ""))):
            return chapter_title(args["book"], args["chapter"])
        if name == "read_thread" and SAFE.match(str(args.get("id", ""))):
            d = read_json(C.THREADS / f"{args['id']}.json")
            return d["title"] if d else f"thread {args['id']}"
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


def system_blocks(tab: str) -> list[dict]:
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
    return [{"type": "text", "text": instructions},
            {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}]


# --- storage ---------------------------------------------------------------------

def chat_path(tab: str):
    kind, key = tab_kind(tab)
    return C.CHATS / f"{kind}-{key}.json"


def load(tab: str) -> dict:
    data = read_json(chat_path(tab))
    if not data:
        data = {"tab": tab, "selected_model": default_model(), "messages": [], "summary": None,
                "session_cost_usd": 0.0, "session_tokens": 0}
    if data["selected_model"] not in [m["id"] for m in chat_models()]:
        data["selected_model"] = default_model()
    return data


def save(tab: str, data: dict) -> None:
    write_json(chat_path(tab), data)


def clear(tab: str) -> dict:
    data = load(tab)
    fresh = {"tab": tab, "selected_model": data["selected_model"], "messages": [], "summary": None,
             "session_cost_usd": 0.0, "session_tokens": 0}
    save(tab, fresh)
    return fresh


def set_model(tab: str, model_id: str) -> dict:
    model_entry(model_id)
    data = load(tab)
    data["selected_model"] = model_id
    save(tab, data)
    return data


# --- the turn -------------------------------------------------------------------

def _history(data: dict) -> list[dict]:
    start = (data.get("summary") or {}).get("covers_until", 0)
    msgs = [{"role": m["role"], "content": m["text"]} for m in data["messages"][start:] if m.get("text")]
    if data.get("summary"):
        # Append-only: the summary arrives as a mid-conversation system message after a user turn.
        msgs = ([{"role": "user", "content": "(Earlier conversation summarized below.)"},
                 {"role": "system", "content": "Summary of the earlier conversation:\n" + data["summary"]["text"]},
                 {"role": "assistant", "content": "Understood."}] + msgs)
    return msgs


def _total_tokens(usage: dict) -> int:
    return sum(usage.get(k, 0) for k in ("input_tokens", "output_tokens",
                                         "cache_creation_input_tokens", "cache_read_input_tokens"))


def send(tab: str, text: str, model_id: str | None = None):
    """Generator of events: {"type": "text"|"tool"|"done"|"error", ...}."""
    data = load(tab)
    model_id = model_id or data["selected_model"]
    entry = model_entry(model_id)
    data["selected_model"] = model_id
    system = system_blocks(tab)
    data["messages"].append({"role": "user", "text": text, "ts": now()})
    save(tab, data)

    messages = _history(data)
    params = {"model": model_id, "max_tokens": C.config()["chat"]["max_output_tokens"],
              "system": system, "tools": TOOLS, "messages": messages}
    if entry.get("effort"):
        params["output_config"] = {"effort": entry["effort"]}

    usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                   "cache_read_input_tokens": 0}
    cost_total, answer_parts, tools_used = 0.0, [], []
    served_by = model_id
    partial = {"usage": None}       # usage of the round in progress, for client disconnects
    try:
        for _ in range(C.config()["chat"].get("max_tool_rounds", 8)):
            if model_id in claude.FALLBACK_MODELS:
                stream_cm = claude.client().beta.messages.stream(
                    betas=[claude.FALLBACK_BETA], fallbacks="default", **params)
            else:
                stream_cm = claude.client().messages.stream(**params)
            with stream_cm as stream:
                for event in stream:
                    partial["usage"] = getattr(stream.current_message_snapshot, "usage", None)
                    if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                        answer_parts.append(event.delta.text)
                        yield {"type": "text", "delta": event.delta.text}
                final = stream.get_final_message()
            partial["usage"] = None
            usage = claude.usage_dict(final.usage)
            served_by = final.model if final.model in C.config()["prices"] else model_id
            cost_total += costs.cost_from_usage(served_by, usage)
            for k in usage_total:
                usage_total[k] += usage[k]

            if final.stop_reason == "refusal":
                answer_parts.append("\n\n*(The model declined to answer this.)*")
                break
            if final.stop_reason != "tool_use":
                break
            # Pass this answer's assistant content back unchanged, then the tool results.
            params["messages"] = params["messages"] + [
                {"role": "assistant", "content": [b.to_dict() for b in final.content]}]
            results = []
            for block in final.content:
                if block.type != "tool_use":
                    continue
                args = block.input if isinstance(block.input, dict) else {}
                label = tool_label(block.name, args)
                tools_used.append({"name": block.name, "input": args, "label": label})
                yield {"type": "tool", "name": block.name, "input": args, "label": label}
                output, is_error = run_tool(block.name, args)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output,
                                **({"is_error": True} if is_error else {})})
            params["messages"] = params["messages"] + [{"role": "user", "content": results}]
            if answer_parts and not answer_parts[-1].endswith("\n"):
                answer_parts.append("\n\n")
                yield {"type": "text", "delta": "\n\n"}
        else:
            answer_parts.append("\n\n*(Stopped after the maximum number of library reads.)*")
    except GeneratorExit:
        # The browser went away mid-answer (closed tab, reload). Anthropic still billed what ran:
        # log finished rounds plus the round in progress, and keep the partial text.
        if partial["usage"] is not None:
            cost_total += costs.cost_from_usage(model_id, claude.usage_dict(partial["usage"]))
        data["messages"].append({"role": "assistant", "text": "".join(answer_parts).strip() + "\n\n*(Interrupted.)*",
                                 "ts": now(), "model": model_id, "model_label": entry["label"],
                                 "cost_usd": round(cost_total, 6), "usage": usage_total, "tools_used": tools_used,
                                 "sources": []})
        data["session_cost_usd"] = round(data["session_cost_usd"] + cost_total, 6)
        save(tab, data)
        if cost_total:
            costs.log(costs.new_run_id("chat"), "chat", "anthropic", model_id,
                      f"chat {tab} (interrupted: client disconnected)", "partial", cost_total, cost_total)
        raise
    except Exception as e:  # noqa: BLE001 - surface API errors in the chat instead of crashing
        if cost_total:
            costs.log(costs.new_run_id("chat"), "chat", "anthropic", model_id, f"chat {tab} (error)",
                      f"{_total_tokens(usage_total):,} tokens", cost_total, cost_total)
        data["session_cost_usd"] += cost_total
        save(tab, data)
        yield {"type": "error", "message": str(e), "session_cost_usd": data["session_cost_usd"]}
        return

    label = next((m["label"] for m in chat_models() if m["id"] == served_by), served_by)
    answer = "".join(answer_parts).strip()
    msg = {"role": "assistant", "text": answer, "ts": now(), "model": served_by,
           "model_label": label, "cost_usd": round(cost_total, 6), "usage": usage_total, "tools_used": tools_used,
           "sources": sources_for(answer, tools_used)}
    data["messages"].append(msg)
    data["session_cost_usd"] = round(data["session_cost_usd"] + cost_total, 6)
    data["session_tokens"] += _total_tokens(usage_total)
    costs.log(costs.new_run_id("chat"), "chat", "anthropic", served_by, f"chat {tab}",
              f"{_total_tokens(usage_total):,} tokens, {len(tools_used)} reads", cost_total, cost_total)
    summary_cost = maybe_summarize(tab, data)
    save(tab, data)
    yield {"type": "done", "message": msg, "session_cost_usd": data["session_cost_usd"],
           "session_tokens": data["session_tokens"], "summary_cost_usd": summary_cost}


def maybe_summarize(tab: str, data: dict) -> float:
    """Summarize older turns (cheap model) once the replayed history gets long. Nothing is deleted:
    every message stays in the file and on screen; only the text sent to the model is condensed."""
    cfg = C.config()["chat"]
    start = (data.get("summary") or {}).get("covers_until", 0)
    active = data["messages"][start:]
    if sum(len(m.get("text", "")) for m in active) / 3.8 < cfg["summarize_after_tokens"]:
        return 0.0
    keep = cfg["keep_recent_turns"] * 2
    cut = len(data["messages"]) - keep
    while cut > start and data["messages"][cut]["role"] != "user":
        cut -= 1                                    # keep the replayed history starting on a user turn
    if cut <= start:
        return 0.0
    old = data["messages"][start:cut]
    transcript = "\n\n".join(f"{m['role'].upper()}: {m['text']}" for m in old)
    prior = (data.get("summary") or {}).get("text", "")
    prompt = ("Summarize this conversation between a card game designer and their research assistant so "
              "it can continue without the full transcript. Keep decisions, the designer's game details, "
              "conclusions, and every chapter or thread link that was cited. Be compact.\n\n"
              + (f"Earlier summary:\n{prior}\n\n" if prior else "") + f"Conversation:\n{transcript}")
    text, cost, usage = claude.call("summarize_chat", [{"role": "user", "content": prompt}], max_tokens=4000)
    data["summary"] = {"text": text, "covers_until": cut, "updated_at": now()}
    data["session_cost_usd"] = round(data["session_cost_usd"] + cost, 6)
    costs.log(costs.new_run_id("chat-summary"), "chat", "anthropic", claude.model_for("summarize_chat"),
              f"chat {tab} history summary", f"{_total_tokens(usage):,} tokens", cost, cost)
    return cost
