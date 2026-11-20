"""Structured book/chapter data for the app (the Markdown files stay the readable source on disk)."""

from __future__ import annotations

import re
import statistics

from lib import config as C
from lib.store import read_json

_PAIR = re.compile(
    r"(\d+)\s+commenters?\s*(?:,|and|with)?\s*\(?(\d+)\s+points?\)?"
    r".*?\bvs\.?\s+(?:about\s+|roughly\s+)?(\d+)\s+(?:commenters?|and)?\s*(?:,|and|with)?\s*\(?(\d+)\s+points?",
    re.I | re.S)


_POINTS_VS = re.compile(r"(\d+)\s+points?\s+vs\.?\s+(\d+)\b", re.I)


def split_support(s: dict) -> dict | None:
    """Points/commenters per side. Prefer the numeric fields; fall back to parsing the weight text
    (older chapters), where the heavier side is named first."""
    if s.get("side_a_points") or s.get("side_b_points"):
        return {"a_points": s.get("side_a_points", 0), "b_points": s.get("side_b_points", 0),
                "a_commenters": s.get("side_a_commenters", 0), "b_commenters": s.get("side_b_commenters", 0)}
    m = _PAIR.search(s.get("weight", ""))
    if m:
        c1, p1, c2, p2 = (int(x) for x in m.groups())
    else:
        m = _POINTS_VS.search(s.get("weight", ""))
        if not m:
            return None
        (p1, p2), c1, c2 = (int(x) for x in m.groups()), 0, 0
    text = s["weight"].lower()
    b_first = text.find("side b") != -1 and (text.find("side a") == -1 or text.find("side b") < text.find("side a"))
    if b_first:
        c1, p1, c2, p2 = c2, p2, c1, p1
    return {"a_points": p1, "b_points": p2, "a_commenters": c1, "b_commenters": c2}


def _stats(book: str) -> tuple[dict, dict]:
    from lib.bookstats import chapter_stats, load_threads
    plan = read_json(C.BOOKS / book / "plan.json")
    threads = load_threads(book)
    return plan, {"stats": chapter_stats(plan, threads), "threads": threads}


def book_overview(book: str) -> dict:
    plan, ctx = _stats(book)
    stats = ctx["stats"]
    cpts = [s["comments_per_thread"] for s in stats.values() if s["threads"]]
    median = statistics.median(cpts) if cpts else 0
    hot = max(40.0, 1.75 * median)
    chapters = []
    for ch in plan["chapters"]:
        s = stats[ch["slug"]]
        saved = read_json(C.BOOKS / book / "chapters" / f"{ch['slug']}.json") or {}
        chapters.append({
            "slug": ch["slug"], "title": ch["title"], "description": ch["description"],
            "one_line": saved.get("one_line"), "tldr": saved.get("tldr"),
            "heat": s["heat"], "comments_per_thread": round(s["comments_per_thread"], 1),
            "threads": s["threads"], "secondary": s["secondary"], "upvotes": s["upvotes"],
            "comments": s["comments"], "date_range": s["date_range"],
            "hot_debate": s["comments_per_thread"] >= hot,
        })
    chapters.sort(key=lambda c: -c["heat"])
    return {"book": book, "thread_count": len(ctx["threads"]), "chapters": chapters,
            "hot_debate_threshold": round(hot, 1)}


def _thread_card(ref: str, d: dict) -> dict:
    img = d.get("image_notes") or {}
    return {"ref": ref, "id": d["id"], "title": d["title"], "url": d["url"], "upvotes": d["upvotes"],
            "comments": d["comments"], "date": d["date"], "flair": d.get("flair"),
            "images": [i["file"] for i in img.get("images", [])][:3],
            "image_note": img.get("what_is_asked")}


def chapter_detail(book: str, slug: str) -> dict | None:
    from lib.bookstats import chapter_refs
    plan, ctx = _stats(book)
    ch = next((c for c in plan["chapters"] if c["slug"] == slug), None)
    saved = read_json(C.BOOKS / book / "chapters" / f"{slug}.json")
    if not ch or not saved:
        return None
    main, sec = chapter_refs(plan, slug, ctx["threads"])
    s = ctx["stats"][slug]
    splits = [{**sp, "support": split_support(sp)} for sp in saved["splits"]]
    return {
        "book": book, "slug": slug, "title": ch["title"], "description": ch["description"],
        "stats": {k: s[k] for k in ("heat", "threads", "secondary", "upvotes", "comments", "date_range")}
                 | {"comments_per_thread": round(s["comments_per_thread"], 1)},
        "tldr": saved["tldr"], "consensus": saved["consensus"],
        "single_thread_agreement": saved.get("single_thread_agreement", []),
        "splits": splits, "design_lessons": saved["design_lessons"],
        "business_lessons": saved["business_lessons"], "debates": saved["debates"],
        "questions": saved["questions"],
        "threads": [_thread_card(r, d) for r, d in main],
        "supporting_threads": [_thread_card(r, d) for r, d in sec],
        "refs": {r: {"title": d["title"], "url": d["url"]} for r, d in main + sec},
    }


def chapter_title(book: str, slug: str) -> str:
    plan = read_json(C.BOOKS / book / "plan.json") or {}
    return next((c["title"] for c in plan.get("chapters", []) if c["slug"] == slug), slug)
