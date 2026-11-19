"""Phase 4: one cheap Claude call per thread -> data/threads/<id>.json.

Metadata (title, url, counts, engagement, flair, image notes) is copied by code, not by the
model; the model writes only the judgment fields. Bulk runs use the Message Batches API
(50% off); `--ids` runs go direct so results come back immediately.
Ledger: extracted_at.
"""

from __future__ import annotations

import json
import time

from lib import claude
from lib import config as C
from lib import costs
from lib.ledger import Ledger, now
from lib.store import read_json, write_json

from .describe_images import notes_path

MAX_POST_CHARS = 6000
MAX_COMMENT_CHARS = 1500
EST_OUTPUT_TOKENS = 1800   # measured ~1,400 before evidence lists; +evidence
CHARS_PER_TOKEN = 3.8

EVIDENCE = {"type": "array", "items": {"type": "string"}}


def _item(text_key: str) -> dict:
    return {"type": "object",
            "properties": {text_key: {"type": "string"}, "evidence": EVIDENCE},
            "required": [text_key, "evidence"], "additionalProperties": False}


SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "consensus": {"type": "array", "items": _item("point")},
        "notable_points": {"type": "array", "items": _item("point")},
        "disagreements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "side_a": {"type": "string"}, "side_a_evidence": EVIDENCE,
                    "side_b": {"type": "string"}, "side_b_evidence": EVIDENCE,
                },
                "required": ["issue", "side_a", "side_a_evidence", "side_b", "side_b_evidence"],
                "additionalProperties": False,
            },
        },
        "design_lessons": {"type": "array", "items": _item("lesson")},
        "business_lessons": {"type": "array", "items": _item("lesson")},
        "content_ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["debate", "question"]},
                    "hook": {"type": "string"},
                    "why_it_lands": {"type": "string"},
                    "evidence": EVIDENCE,
                },
                "required": ["type", "hook", "why_it_lands", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topics", "summary", "consensus", "notable_points", "disagreements",
                 "design_lessons", "business_lessons", "content_ideas"],
    "additionalProperties": False,
}


def thread_path(post_id: str):
    return C.THREADS / f"{post_id}.json"


def _image_notes(post_id: str) -> dict | None:
    notes = read_json(notes_path(post_id))
    if not notes:
        return None
    return {"model": notes["model"], "design_relevant": notes["design_relevant"],
            "what_is_asked": notes["what_is_asked"],
            "images": [{"file": f"data/images/{post_id}/{i['file']}", "description": i["description"]}
                       for i in notes["images"]]}


_AUTHORS: dict | None = None


def _author_index() -> dict:
    """comment/post id -> author, from saved raw Apify runs (threads fetched before authors
    were stored in data/raw/<sub>/<id>.json)."""
    global _AUTHORS
    if _AUTHORS is None:
        _AUTHORS = {}
        for f in (C.RAW / "_runs").glob("*.json"):
            for i in (read_json(f) or {}).get("items", []):
                rid = str(i.get("id") or "").split("_")[-1]
                author = i.get("author") or i.get("username") or i.get("authorName")
                if rid and author:
                    _AUTHORS[rid] = author
    return _AUTHORS


def numbered_comments(raw: dict) -> list[dict]:
    """Comments with stable IDs C1..Cn, author, and whether the author is the original poster."""
    post = raw["post"]
    op = post.get("author") or _author_index().get(post["id"])
    out = []
    for n, c in enumerate(raw["comments"], start=1):
        author = c.get("author") or _author_index().get(c["id"])
        out.append({**c, "cid": f"C{n}", "author": author,
                    "is_op": bool(op and author and author == op)})
    return out


def render_thread(raw: dict, image_notes: dict | None) -> str:
    """The thread as plain text for the model: post, numbers, image notes, numbered comment tree."""
    p = raw["post"]
    comments = numbered_comments(raw)
    community = [c for c in comments if not c["is_op"]]
    lines = [
        f"Subreddit: r/{p['subreddit']}",
        f"Title: {p['title']}",
        f"Flair: {p.get('flair') or '(none)'}",
        f"Posted: {p['created_utc'][:10]}   Upvotes: {p['upvotes']}   Comments: {p['num_comments']}"
        f"   Post type: {p['post_type']}",
        "",
        "Post text:",
        (p.get("selftext") or "(no text)")[:MAX_POST_CHARS],
    ]
    if image_notes:
        lines += ["", f"Images ({'design-relevant' if image_notes['design_relevant'] else 'not design-relevant'}); "
                      f"what the poster asks: {image_notes['what_is_asked']}"]
        for n, img in enumerate(image_notes["images"], start=1):
            lines.append(f"[Image {n}] {img['description']}")
    total = sum(max(c["score"], 0) for c in community)
    lines += ["", f"Top comments (Best order, {len(comments)} shown; community comments total {total} points):"]
    for c in comments:
        indent = "    " * min(c["depth"], 4)
        body = " ".join(c["body"].split())[:MAX_COMMENT_CHARS]
        tag = " [OP]" if c["is_op"] else ""
        lines.append(f"{indent}- {c['cid']} ({c['score']} pts){tag} {body}")
    return "\n".join(lines)


def support(evidence: list[str], by_cid: dict, total_points: int) -> dict:
    """Points, comments and distinct commenters behind a claim. OP replies never count."""
    cited = [by_cid[e] for e in dict.fromkeys(evidence) if e in by_cid and not by_cid[e]["is_op"]]
    points = sum(max(c["score"], 0) for c in cited)
    authors = {c["author"] or c["cid"] for c in cited}
    return {"comments": len(cited), "commenters": len(authors), "points": points,
            "share": round(points / total_points, 3) if total_points else 0.0}


def apply_support(raw: dict, fields: dict) -> dict:
    """Attach support counts to every claim and enforce the consensus bar in code."""
    cfg = C.config().get("extraction", {})
    min_commenters = cfg.get("consensus_min_commenters", 2)
    min_share = cfg.get("consensus_min_share", 0.15)
    comments = numbered_comments(raw)
    by_cid = {c["cid"]: c for c in comments}
    total = sum(max(c["score"], 0) for c in comments if not c["is_op"])

    consensus, notable = [], []
    for item in fields["consensus"]:
        item["support"] = support(item["evidence"], by_cid, total)
        s_ = item["support"]
        if s_["commenters"] >= min_commenters and s_["share"] >= min_share:
            consensus.append(item)
        else:
            item["demoted_from_consensus"] = True
            notable.append(item)
    for item in fields["notable_points"]:
        item["support"] = support(item["evidence"], by_cid, total)
        notable.append(item)
    fields["consensus"] = sorted(consensus, key=lambda x: -x["support"]["points"])
    fields["notable_points"] = sorted(notable, key=lambda x: -x["support"]["points"])

    splits = []
    for d in fields["disagreements"]:
        d["side_a_support"] = support(d["side_a_evidence"], by_cid, total)
        d["side_b_support"] = support(d["side_b_evidence"], by_cid, total)
        if d["side_a_support"]["comments"] and d["side_b_support"]["comments"]:
            splits.append(d)  # a "split" with no cited comments on one side is dropped
    fields["disagreements"] = splits
    for key in ("design_lessons", "business_lessons", "content_ideas"):
        for item in fields[key]:
            item["support"] = support(item["evidence"], by_cid, total)
    fields["comment_points_total"] = total
    fields["comment_ids"] = {c["cid"]: c["id"] for c in comments}
    return fields


def build_messages(raw: dict, image_notes: dict | None) -> list:
    return [{"role": "user", "content": render_thread(raw, image_notes)}]


def system_prompt() -> list:
    # Identical on every call, so it is cached across the batch / run.
    text = (C.PROMPTS / "extract_thread.md").read_text(encoding="utf-8")
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def estimate(raws: dict[str, dict], batch: bool) -> tuple[float, int, int]:
    model = claude.model_for("extract")
    sys_tokens = int(len((C.PROMPTS / "extract_thread.md").read_text()) / CHARS_PER_TOKEN) + 400  # + schema
    usd = tin = tout = 0
    for pid, raw in raws.items():
        i = sys_tokens + int(len(render_thread(raw, _image_notes(pid))) / CHARS_PER_TOKEN)
        usd += costs.claude_cost(model, i, EST_OUTPUT_TOKENS, batch=batch)
        tin += i
        tout += EST_OUTPUT_TOKENS
    return usd, tin, tout


def assemble(raw: dict, fields: dict, model: str, cost: float) -> dict:
    fields = apply_support(raw, fields)
    p = raw["post"]
    pid = p["id"]
    return {
        "id": pid,
        "title": p["title"],
        "url": p["url"],
        "subreddit": p["subreddit"],
        "date": p["created_utc"][:10],
        "upvotes": p["upvotes"],
        "comments": p["num_comments"],
        "engagement_score": raw.get("engagement_score"),
        "flair": p.get("flair"),
        "post_type": p["post_type"],
        **fields,
        "image_notes": _image_notes(pid),
        "extracted_at": now(),
        "extract_model": model,
        "extract_cost_usd": round(cost, 6),
    }


def _save(pid: str, sub: str, raw: dict, fields: dict, model: str, cost: float, ledger: Ledger) -> None:
    write_json(thread_path(pid), assemble(raw, fields, model, cost))
    ledger.mark(pid, extracted_at=now(), extract_model=model)
    ledger.save()


def run(sub: str, ids: list[str] | None = None, use_batch: bool | None = None) -> None:
    ledger = Ledger()
    model = claude.model_for("extract")
    if ids:
        targets = ids
        use_batch = False if use_batch is None else use_batch
    else:
        targets = sorted(i for i, e in ledger.threads.items()
                         if e.get("subreddit") == sub and e.get("fetched_at") and not e.get("extracted_at"))
        use_batch = C.config().get("batch", True) if use_batch is None else use_batch
    raws = {}
    for pid in targets:
        raw = read_json(C.RAW / sub / f"{pid}.json")
        if not raw:
            print(f"  {pid}: not a fetched r/{sub} thread; ignored.")
            continue
        if not ids and ledger.get(pid).get("images_done") is None:
            print(f"  {pid}: images step not done yet; run `rl images --sub {sub}` first. Skipped.")
            continue
        raws[pid] = raw
    if not raws:
        print("Nothing to extract.")
        return

    usd, tin, tout = estimate(raws, use_batch)
    est = costs.Estimate(f"Phase 4 extraction for r/{sub}")
    est.add(f"{len(raws)} threads on {model}{' (Batch API, 50% off)' if use_batch else ''}",
            f"~{tin:,} in / {tout:,} out", usd)
    if not costs.confirm(est):
        print("Cancelled. Nothing was sent to Claude.")
        return

    run_id = costs.new_run_id("phase4-extract")
    if use_batch:
        _run_batch(sub, raws, model, usd, run_id, ledger)
        return

    spend = claude.Spend()
    try:
        for pid, raw in raws.items():
            try:
                fields, cost, usage = claude.call("extract", build_messages(raw, _image_notes(pid)),
                                                  system=system_prompt(), schema=SCHEMA, max_tokens=16000)
            except claude.ClaudeError as e:
                print(f"  {pid}: {e}")
                continue
            spend.add(cost, usage)
            _save(pid, sub, raw, fields, model, cost, ledger)
            print(f"  {pid} {raw['post']['title'][:60]}  ${cost:.5f}")
    finally:
        if spend.calls:
            costs.log(run_id, "phase4", "anthropic", model, f"r/{sub} extraction ({spend.calls} threads, direct)",
                      spend.units(), usd, spend.usd)
    print(f"\nExtracted {spend.calls} threads for ${spend.usd:.5f} (estimate ${usd:.5f}).")


def _run_batch(sub, raws, model, est_usd, run_id, ledger) -> None:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = []
    for pid, raw in raws.items():
        params = claude.build_request("extract", build_messages(raw, _image_notes(pid)),
                                      system=system_prompt(), schema=SCHEMA, max_tokens=16000)
        requests.append(Request(custom_id=pid, params=MessageCreateParamsNonStreaming(**params)))
    batch = claude.client().messages.batches.create(requests=requests)
    pending = C.DATA / "pending_batches.json"
    jobs = read_json(pending, default={})
    jobs[batch.id] = {"sub": sub, "run_id": run_id, "model": model, "est_usd": est_usd,
                      "created_at": now(), "count": len(requests)}
    write_json(pending, jobs)
    print(f"Submitted batch {batch.id} with {len(requests)} threads. Waiting for results "
          f"(usually minutes; Ctrl+C is safe, `rl extract-collect` picks it up later)...")
    collect_batch(batch.id)


def collect_batch(batch_id: str) -> None:
    pending = C.DATA / "pending_batches.json"
    jobs = read_json(pending, default={})
    job = jobs.get(batch_id)
    if not job:
        print(f"Unknown batch {batch_id}.")
        return
    cl = claude.client()
    while True:
        b = cl.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        c = b.request_counts
        print(f"  batch {batch_id}: {c.succeeded} done, {c.processing} processing, {c.errored} errored")
        time.sleep(30)

    ledger = Ledger()
    spend = claude.Spend()
    failed = []
    for result in cl.messages.batches.results(batch_id):
        pid = result.custom_id
        if result.result.type != "succeeded":
            failed.append((pid, result.result.type))
            continue
        msg = result.result.message
        usage = claude.usage_dict(msg.usage)
        cost = costs.cost_from_usage(job["model"], usage, batch=True)
        spend.add(cost, usage)
        if msg.stop_reason != "end_turn":
            failed.append((pid, msg.stop_reason))
            continue
        text = next((blk.text for blk in msg.content if blk.type == "text"), "")
        raw = read_json(C.RAW / job["sub"] / f"{pid}.json")
        _save(pid, job["sub"], raw, json.loads(text), job["model"], cost, ledger)
    costs.log(job["run_id"], "phase4", "anthropic", job["model"],
              f"r/{job['sub']} extraction ({spend.calls} threads, batch)", spend.units(),
              job["est_usd"], spend.usd, external_id=batch_id)
    jobs.pop(batch_id)
    write_json(pending, jobs)
    print(f"\nBatch done: {spend.calls} threads extracted for ${spend.usd:.5f} "
          f"(estimate ${job['est_usd']:.5f}). Failed: {len(failed)} {failed or ''}")


def compare(sub: str, ids: list[str], models: list[str]) -> None:
    """Run the same threads on several models (direct calls) into data/threads/_compare/<model>/.

    Does not touch data/threads/<id>.json or the ledger. One estimate, one y/N for all models.
    """
    models = [claude.resolve_model(m) for m in models]
    raws = {pid: read_json(C.RAW / sub / f"{pid}.json") for pid in ids}
    raws = {k: v for k, v in raws.items() if v}
    est = costs.Estimate(f"Phase 4 model comparison on {len(raws)} r/{sub} threads")
    sys_tokens = int(len((C.PROMPTS / "extract_thread.md").read_text()) / CHARS_PER_TOKEN) + 600
    for m in models:
        tin = sum(sys_tokens + int(len(render_thread(r, _image_notes(p))) / CHARS_PER_TOKEN) for p, r in raws.items())
        tout = EST_OUTPUT_TOKENS * len(raws)
        est.add(f"{len(raws)} threads on {m}", f"~{tin:,} in / {tout:,} out", costs.claude_cost(m, tin, tout))
    if not costs.confirm(est):
        print("Cancelled. Nothing was sent to Claude.")
        return
    for m in models:
        spend = claude.Spend()
        run_id = costs.new_run_id("phase4-compare")
        try:
            for pid, raw in raws.items():
                try:
                    fields, cost, usage = claude.call("extract", build_messages(raw, _image_notes(pid)),
                                                      system=system_prompt(), schema=SCHEMA,
                                                      max_tokens=16000, model=m)
                except claude.ClaudeError as e:
                    print(f"  {m} {pid}: {e}")
                    continue
                spend.add(cost, usage)
                write_json(C.THREADS / "_compare" / m / f"{pid}.json", assemble(raw, fields, m, cost))
                print(f"  {m:<18} {pid} {raw['post']['title'][:50]}  ${cost:.5f}")
        finally:
            if spend.calls:
                costs.log(run_id, "phase4", "anthropic", m, f"r/{sub} extraction comparison ({spend.calls} threads)",
                          spend.units(), est.items[models.index(m)].usd, spend.usd)
        print(f"{m}: ${spend.usd:.5f} for {spend.calls} threads")
