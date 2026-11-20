"""Book statistics computed in code from thread notes and plan.json (no model calls).

Shared by the pipeline and the app. This module must never import `anthropic`, `lib.claude`
or `lib.costs`, so the app's chat process cannot reach the Anthropic API through it.
"""

from __future__ import annotations

from . import config as C
from .store import read_json


def book_dir(sub: str):
    return C.BOOKS / sub


def plan_path(sub: str):
    return book_dir(sub) / "plan.json"


def load_threads(sub: str) -> dict[str, dict]:
    out = {}
    for f in sorted(C.THREADS.glob("*.json")):
        d = read_json(f)
        if d and d.get("subreddit") == sub:
            out[d["id"]] = d
    return out


def heat_table(plan: dict, threads: dict[str, dict]) -> list[dict]:
    """Per-chapter stats computed in code. Heat = sum of engagement (secondary threads count half)."""
    rows = []
    for ch in plan["chapters"]:
        prim = [threads[i] for i, a in plan["assignments"].items() if a["primary"] == ch["slug"] and i in threads]
        sec = [threads[i] for i, a in plan["assignments"].items() if a.get("secondary") == ch["slug"] and i in threads]
        both = prim + sec
        dates = sorted(d["date"] for d in both)
        rows.append({
            "slug": ch["slug"], "title": ch["title"],
            "threads": len(prim), "secondary": len(sec),
            "heat": round(sum(d["engagement_score"] for d in prim) + 0.5 * sum(d["engagement_score"] for d in sec), 2),
            "upvotes": sum(d["upvotes"] for d in prim), "comments": sum(d["comments"] for d in prim),
            "date_range": f"{dates[0]} to {dates[-1]}" if dates else "",
        })
    rows.sort(key=lambda r: -r["heat"])
    return rows


def chapter_refs(plan: dict, slug: str, threads: dict) -> tuple[list, list]:
    """(main, secondary) as lists of (ref, thread), each sorted by engagement."""
    by_eng = lambda d: -(d["engagement_score"] or 0)
    main = sorted((threads[i] for i, a in plan["assignments"].items() if a["primary"] == slug), key=by_eng)
    sec = sorted((threads[i] for i, a in plan["assignments"].items() if a.get("secondary") == slug), key=by_eng)
    return [(f"T{n}", d) for n, d in enumerate(main, 1)], [(f"S{n}", d) for n, d in enumerate(sec, 1)]


def chapter_stats(plan: dict, threads: dict) -> dict:
    rows = {r["slug"]: r for r in heat_table(plan, threads)}
    for r in rows.values():
        r["comments_per_thread"] = r["comments"] / r["threads"] if r["threads"] else 0.0
    return rows
