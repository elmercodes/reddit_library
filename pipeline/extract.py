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
EST_OUTPUT_TOKENS = 1100
CHARS_PER_TOKEN = 3.8

SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "consensus": {"type": "array", "items": {"type": "string"}},
        "disagreements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "side_a": {"type": "string"},
                    "side_b": {"type": "string"},
                },
                "required": ["issue", "side_a", "side_b"],
                "additionalProperties": False,
            },
        },
        "design_lessons": {"type": "array", "items": {"type": "string"}},
        "business_lessons": {"type": "array", "items": {"type": "string"}},
        "content_ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["debate", "question"]},
                    "hook": {"type": "string"},
                    "why_it_lands": {"type": "string"},
                },
                "required": ["type", "hook", "why_it_lands"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topics", "summary", "consensus", "disagreements", "design_lessons",
                 "business_lessons", "content_ideas"],
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


def render_thread(raw: dict, image_notes: dict | None) -> str:
    """The thread as plain text for the model: post, numbers, image notes, comment tree."""
    p = raw["post"]
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
    lines += ["", f"Top comments (Best order, {len(raw['comments'])} shown):"]
    for c in raw["comments"]:
        indent = "    " * min(c["depth"], 4)
        body = " ".join(c["body"].split())[:MAX_COMMENT_CHARS]
        lines.append(f"{indent}- ({c['score']} pts) {body}")
    return "\n".join(lines)


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
