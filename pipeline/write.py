"""Phase 5, Steps B and C: write each chapter (Opus, Batch API) and the book index (code).

Opus writes the judgment sections as structured JSON with [T1]/[S1] citations; code renders the
Markdown, turns citations into Reddit links, and builds "How hot it is" and the Threads list, so
numbers and links are never model-generated.

Files: books/<sub>/chapters/<slug>.json (model output + metadata), books/<sub>/chapters/<slug>.md,
       books/<sub>/index.md (YAML front matter = chapter list for routing).
"""

from __future__ import annotations

import json
import re
import time

import yaml

from lib import claude
from lib import config as C
from lib import costs
from lib.ledger import now
from lib.store import read_json, write_json, write_text

from .cluster import heat_table, load_threads, plan_path

EST_OUTPUT_TOKENS = 4500          # per chapter at high effort; measured 4,022 avg on r/homemadeTCGs (16 chapters)
CHAPTER_MAX_TOKENS = 32000
PENDING = C.DATA / "pending_chapter_batches.json"

_IDEA = {"type": "object",
         "properties": {"hook": {"type": "string"}, "why": {"type": "string"},
                        "threads": {"type": "array", "items": {"type": "string"}}},
         "required": ["hook", "why", "threads"], "additionalProperties": False}


def _cited(key: str) -> dict:
    return {"type": "object",
            "properties": {key: {"type": "string"}, "threads": {"type": "array", "items": {"type": "string"}}},
            "required": [key, "threads"], "additionalProperties": False}


SCHEMA = {
    "type": "object",
    "properties": {
        "tldr": {"type": "string"},
        "one_line": {"type": "string"},
        "consensus": {"type": "array", "items": _cited("point")},
        "splits": {"type": "array", "items": {
            "type": "object",
            "properties": {"issue": {"type": "string"}, "side_a": {"type": "string"},
                           "side_b": {"type": "string"}, "weight": {"type": "string"},
                           "threads": {"type": "array", "items": {"type": "string"}}},
            "required": ["issue", "side_a", "side_b", "weight", "threads"], "additionalProperties": False}},
        "design_lessons": {"type": "array", "items": _cited("lesson")},
        "business_lessons": {"type": "array", "items": _cited("lesson")},
        "debates": {"type": "array", "items": _IDEA},
        "questions": {"type": "array", "items": _IDEA},
    },
    "required": ["tldr", "one_line", "consensus", "splits", "design_lessons", "business_lessons",
                 "debates", "questions"],
    "additionalProperties": False,
}


def chapters_dir(sub: str):
    return C.BOOKS / sub / "chapters"


# --- inputs -------------------------------------------------------------------

def _strip(items: list, key: str) -> list:
    return [{key: i[key], "support": {k: i["support"][k] for k in ("commenters", "points", "share")}}
            for i in items]


def main_thread_input(d: dict) -> dict:
    x = {k: d[k] for k in ("title", "date", "upvotes", "comments", "engagement_score", "flair", "topics", "summary")}
    x["comment_points_total"] = d.get("comment_points_total")
    x["consensus"] = _strip(d["consensus"], "point")
    x["notable_points"] = _strip(d["notable_points"], "point")
    x["splits"] = [{"issue": s["issue"], "side_a": s["side_a"], "side_b": s["side_b"],
                    "side_a_support": s["side_a_support"], "side_b_support": s["side_b_support"]}
                   for s in d["disagreements"]]
    x["design_lessons"] = _strip(d["design_lessons"], "lesson")
    x["business_lessons"] = _strip(d["business_lessons"], "lesson")
    x["content_ideas"] = [{"type": i["type"], "hook": i["hook"], "why_it_lands": i["why_it_lands"],
                           "support": {k: i["support"][k] for k in ("commenters", "points")}}
                          for i in d["content_ideas"]]
    if d.get("image_notes"):
        x["images"] = {"what_is_asked": d["image_notes"]["what_is_asked"],
                       "descriptions": [i["description"][:400] for i in d["image_notes"]["images"]]}
    return x


def secondary_thread_input(d: dict) -> dict:
    return {k: d[k] for k in ("title", "date", "upvotes", "comments", "engagement_score", "topics", "summary")}


def chapter_refs(plan: dict, slug: str, threads: dict) -> tuple[list, list]:
    """(main, secondary) as lists of (ref, thread), each sorted by engagement."""
    by_eng = lambda d: -(d["engagement_score"] or 0)
    main = sorted((threads[i] for i, a in plan["assignments"].items() if a["primary"] == slug), key=by_eng)
    sec = sorted((threads[i] for i, a in plan["assignments"].items() if a.get("secondary") == slug), key=by_eng)
    return [(f"T{n}", d) for n, d in enumerate(main, 1)], [(f"S{n}", d) for n, d in enumerate(sec, 1)]


def chapter_request(sub: str, plan: dict, chapter: dict, threads: dict) -> dict:
    main, sec = chapter_refs(plan, chapter["slug"], threads)
    parts = [f"Subreddit: r/{sub}", f"Chapter: {chapter['title']}", f"Description: {chapter['description']}",
             "", "Main threads:"]
    parts += [f"{ref}: {json.dumps(main_thread_input(d), ensure_ascii=False)}" for ref, d in main]
    if sec:
        parts += ["", "Supporting threads (main chapter elsewhere; summary only):"]
        parts += [f"{ref}: {json.dumps(secondary_thread_input(d), ensure_ascii=False)}" for ref, d in sec]
    system = [{"type": "text", "text": (C.PROMPTS / "write_chapter.md").read_text(encoding="utf-8"),
               "cache_control": {"type": "ephemeral"}}]
    return claude.build_request("books", [{"role": "user", "content": "\n".join(parts)}],
                                system=system, schema=SCHEMA, max_tokens=CHAPTER_MAX_TOKENS)


# --- estimate (exact input via the free token-counting endpoint) ---------------

def count_input_tokens(params: dict) -> int:
    body = {k: params[k] for k in ("model", "messages", "system") if k in params}
    if "output_config" in params:
        body["output_config"] = params["output_config"]
    try:
        return claude.client().messages.count_tokens(**body).input_tokens
    except Exception:  # noqa: BLE001 - fall back if output_config isn't accepted for counting
        body.pop("output_config", None)
        return claude.client().messages.count_tokens(**body).input_tokens + 1500  # + schema


def estimate(sub: str, plan: dict, threads: dict, batch: bool) -> tuple[float, list[dict]]:
    model = claude.model_for("books")
    rows, total = [], 0.0
    for ch in plan["chapters"]:
        params = chapter_request(sub, plan, ch, threads)
        tin = count_input_tokens(params)
        usd = costs.claude_cost(model, tin, EST_OUTPUT_TOKENS, batch=batch)
        rows.append({"slug": ch["slug"], "input_tokens": tin, "usd": usd})
        total += usd
    return total, rows


# --- rendering (code) ---------------------------------------------------------

def _link_refs(text: str, refmap: dict) -> str:
    def repl(m):
        refs = [r.strip() for r in m.group(1).split(",")]
        if not all(r in refmap for r in refs):
            return m.group(0)
        return "(" + ", ".join(f"[{r}]({refmap[r]['url']})" for r in refs) + ")"
    return re.sub(r"\[((?:[TS]\d+)(?:\s*,\s*[TS]\d+)*)\]", repl, text)


def _cite(threads_refs: list, refmap: dict) -> str:
    refs = [r for r in threads_refs if r in refmap]
    return " " + _link_refs("[" + ", ".join(refs) + "]", refmap) if refs else ""


def render_chapter(sub: str, chapter: dict, out: dict, main: list, sec: list, stats: dict) -> str:
    refmap = {ref: d for ref, d in main + sec}
    L = [f"# {chapter['title']}", "", f"*r/{sub} · {chapter['description']}*", "",
         "## TL;DR", "", _link_refs(out["tldr"], refmap), "",
         "## How hot it is", "",
         f"- **Threads:** {stats['threads']} main" + (f" + {stats['secondary']} supporting" if stats["secondary"] else ""),
         f"- **Engagement:** {stats['upvotes']:,} upvotes, {stats['comments']:,} comments "
         f"({stats['comments_per_thread']:.0f} comments per thread)",
         f"- **Heat score:** {stats['heat']:.1f}",
         f"- **Date range:** {stats['date_range']}", ""]

    L += ["## Consensus", ""]
    L += [f"- {_link_refs(c['point'], refmap)}{_cite(c['threads'], refmap)}" for c in out["consensus"]] or \
         ["- No clear shared ground across these threads."]
    L += ["", "## Hot takes and splits", ""]
    if out["splits"]:
        for s in out["splits"]:
            L += [f"### {s['issue']}", "",
                  f"- **One side:** {_link_refs(s['side_a'], refmap)}",
                  f"- **Other side:** {_link_refs(s['side_b'], refmap)}",
                  f"- **Weight:** {_link_refs(s['weight'], refmap)}{_cite(s['threads'], refmap)}", ""]
    else:
        L += ["- No real splits in these threads.", ""]

    L += ["## Design lessons", ""]
    L += [f"- {_link_refs(x['lesson'], refmap)}{_cite(x['threads'], refmap)}" for x in out["design_lessons"]] or ["- None."]
    if out["business_lessons"]:
        L += ["", "## Business and Kickstarter lessons", ""]
        L += [f"- {_link_refs(x['lesson'], refmap)}{_cite(x['threads'], refmap)}" for x in out["business_lessons"]]

    L += ["", "## Content ideas", "", "### Debates to weigh in on", ""]
    L += [f"- **\"{i['hook']}\"** {_link_refs(i['why'], refmap)}{_cite(i['threads'], refmap)}" for i in out["debates"]] or ["- None."]
    L += ["", "### Recurring questions to answer", ""]
    L += [f"- **\"{i['hook']}\"** {_link_refs(i['why'], refmap)}{_cite(i['threads'], refmap)}" for i in out["questions"]] or ["- None."]

    L += ["", "## Threads", ""]
    for ref, d in main:
        L.append(f"- **{ref}** [{d['title']}]({d['url']}) · {d['upvotes']} upvotes · {d['comments']} comments · {d['date']}")
        if d.get("image_notes"):
            imgs = " ".join(f"![]({_img_rel(i['file'])})" for i in d["image_notes"]["images"][:3])
            L.append(f"  - *Images:* {d['image_notes']['what_is_asked']}")
            L.append(f"  - {imgs}")
    if sec:
        L += ["", "**Also relevant** (main chapter elsewhere):", ""]
        L += [f"- **{ref}** [{d['title']}]({d['url']}) · {d['upvotes']} upvotes · {d['comments']} comments · {d['date']}"
              for ref, d in sec]
    return "\n".join(L) + "\n"


def _img_rel(path: str) -> str:
    # chapter files live at books/<sub>/chapters/<slug>.md
    return "../../../" + path


def chapter_stats(plan: dict, threads: dict) -> dict:
    rows = {r["slug"]: r for r in heat_table(plan, threads)}
    for r in rows.values():
        r["comments_per_thread"] = r["comments"] / r["threads"] if r["threads"] else 0.0
    return rows


def save_chapter(sub: str, plan: dict, chapter: dict, threads: dict, out: dict, cost: float, model: str) -> None:
    main, sec = chapter_refs(plan, chapter["slug"], threads)
    stats = chapter_stats(plan, threads)[chapter["slug"]]
    write_json(chapters_dir(sub) / f"{chapter['slug']}.json", {
        "slug": chapter["slug"], "written_at": now(), "model": model, "cost_usd": round(cost, 6),
        "refs": {ref: d["id"] for ref, d in main + sec}, **out})
    write_text(chapters_dir(sub) / f"{chapter['slug']}.md", render_chapter(sub, chapter, out, main, sec, stats))


def write_index(sub: str) -> None:
    """Step C (free): books/<sub>/index.md sorted by heat, with comments per thread."""
    plan = read_json(plan_path(sub))
    threads = load_threads(sub)
    stats = chapter_stats(plan, threads)
    order = sorted(plan["chapters"], key=lambda c: -stats[c["slug"]]["heat"])
    front = {"subreddit": sub, "updated_at": now(), "chapters": []}
    for c in order:
        front["chapters"].append({
            "slug": c["slug"], "title": c["title"], "description": c["description"], "keywords": c["keywords"],
            "threads": [i for i, a in plan["assignments"].items() if a["primary"] == c["slug"]],
            "secondary_threads": [i for i, a in plan["assignments"].items() if a.get("secondary") == c["slug"]],
        })
    L = ["---", yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip(), "---", "",
         f"# r/{sub}", "", f"{len(threads)} threads in {len(order)} chapters, sorted by heat.", "",
         "| Chapter | Heat | Comments / thread | Threads | TL;DR |", "|---|---|---|---|---|"]
    for c in order:
        s = stats[c["slug"]]
        ch = read_json(chapters_dir(sub) / f"{c['slug']}.json") or {}
        one = (ch.get("one_line") or "*(not written yet)*").replace("|", "/")
        L.append(f"| [{c['title']}](chapters/{c['slug']}.md) | {s['heat']:.1f} | {s['comments_per_thread']:.0f} | "
                 f"{s['threads']}" + (f" +{s['secondary']}" if s["secondary"] else "") + f" | {one} |")
    write_text(C.BOOKS / sub / "index.md", "\n".join(L) + "\n")


# --- the step -----------------------------------------------------------------

def run(sub: str, slugs: list[str] | None = None, use_batch: bool = True) -> None:
    plan = read_json(plan_path(sub))
    if not plan or plan["status"] != "approved":
        print(f"No approved chapter plan for r/{sub}. Run `rl chapters --sub {sub}` and approve it first.")
        return
    threads = load_threads(sub)
    todo = [c for c in plan["chapters"] if (not slugs or c["slug"] in slugs)
            and (slugs or not (chapters_dir(sub) / f"{c['slug']}.json").exists())]
    if not todo:
        print("All chapters are written. Use --slugs to rewrite specific ones.")
        write_index(sub)
        return
    sub_plan = dict(plan, chapters=todo)
    model = claude.model_for("books")
    print(f"Counting input tokens for {len(todo)} chapters (free)...")
    usd, rows = estimate(sub, sub_plan, threads, use_batch)
    est = costs.Estimate(f"Phase 5 Step B: write {len(todo)} r/{sub} chapters")
    for r in rows:
        est.add(f"{r['slug']}", f"{r['input_tokens']:,} in / ~{EST_OUTPUT_TOKENS:,} out", r["usd"])
    print(f"Model {model}, effort {claude.effort_for('books')}"
          f"{', Batch API (50% off)' if use_batch else ''}. Input counts are exact; output is estimated.")
    if not costs.confirm(est):
        print("Cancelled. Nothing was sent to Claude.")
        return

    run_id = costs.new_run_id("phase5-write")
    if not use_batch:
        spend = claude.Spend()
        try:
            for ch in todo:
                params = chapter_request(sub, plan, ch, threads)
                out, cost, usage = claude.call("books", params["messages"], system=params["system"],
                                               schema=SCHEMA, max_tokens=CHAPTER_MAX_TOKENS)
                spend.add(cost, usage)
                save_chapter(sub, plan, ch, threads, out, cost, model)
                print(f"  {ch['slug']}  ${cost:.4f}")
        finally:
            if spend.calls:
                costs.log(run_id, "phase5", "anthropic", model, f"r/{sub} chapters ({spend.calls}, direct)",
                          spend.units(), usd, spend.usd)
        write_index(sub)
        return

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    requests = [Request(custom_id=ch["slug"],
                        params=MessageCreateParamsNonStreaming(**chapter_request(sub, plan, ch, threads)))
                for ch in todo]
    batch = claude.client().messages.batches.create(requests=requests)
    jobs = read_json(PENDING, default={})
    jobs[batch.id] = {"sub": sub, "run_id": run_id, "model": model, "est_usd": usd, "created_at": now(),
                      "slugs": [c["slug"] for c in todo]}
    write_json(PENDING, jobs)
    print(f"Submitted batch {batch.id} with {len(requests)} chapters. Waiting "
          f"(Ctrl+C is safe; `rl write-collect` picks it up later)...")
    collect(batch.id)


def collect(batch_id: str) -> None:
    jobs = read_json(PENDING, default={})
    job = jobs.get(batch_id)
    if not job:
        print(f"Unknown batch {batch_id}.")
        return
    cl = claude.client()
    while (b := cl.messages.batches.retrieve(batch_id)).processing_status != "ended":
        c = b.request_counts
        print(f"  batch {batch_id}: {c.succeeded} done, {c.processing} processing, {c.errored} errored")
        time.sleep(30)
    plan = read_json(plan_path(job["sub"]))
    threads = load_threads(job["sub"])
    chapters = {c["slug"]: c for c in plan["chapters"]}
    spend, failed = claude.Spend(), []
    for result in cl.messages.batches.results(batch_id):
        slug = result.custom_id
        if result.result.type != "succeeded":
            failed.append((slug, result.result.type))
            continue
        msg = result.result.message
        usage = claude.usage_dict(msg.usage)
        cost = costs.cost_from_usage(job["model"], usage, batch=True)
        spend.add(cost, usage)
        if msg.stop_reason != "end_turn":
            failed.append((slug, msg.stop_reason))
            continue
        out = json.loads(next(blk.text for blk in msg.content if blk.type == "text"))
        save_chapter(job["sub"], plan, chapters[slug], threads, out, cost, job["model"])
    costs.log(job["run_id"], "phase5", "anthropic", job["model"],
              f"r/{job['sub']} chapters ({spend.calls}, batch)", spend.units(), job["est_usd"], spend.usd,
              external_id=batch_id)
    jobs.pop(batch_id)
    write_json(PENDING, jobs)
    write_index(job["sub"])
    print(f"\nBatch done: {spend.calls - len([f for f in failed if f[1] != 'errored'])} chapters written for "
          f"${spend.usd:.4f} (estimate ${job['est_usd']:.4f}). Failed: {failed or 'none'}")
    print(f"Book index: books/{job['sub']}/index.md")
