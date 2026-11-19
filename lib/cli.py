"""Command line entry point: `uv run rl <command>`."""

from __future__ import annotations

import importlib
import platform
import shutil
import subprocess
import sys

import typer

from . import config as C

app = typer.Typer(help="Reddit Library: collect, summarize, and chat with subreddit books.",
                  no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Reddit Library commands. Every paid step shows a cost estimate and waits for y/N."""


def _ok(flag: bool) -> str:
    return "OK " if flag else "-- "


@app.command("env-check")
def env_check() -> None:
    """Check tools, dependencies, folders, config, and which secrets are set (never their values)."""
    problems = 0

    py_ok = sys.version_info >= (3, 10)
    problems += not py_ok
    print(f"{_ok(py_ok)} Python {platform.python_version()} (need 3.10+)")

    for tool in ("uv", "git"):
        path = shutil.which(tool)
        version = ""
        if path:
            version = subprocess.run([tool, "--version"], capture_output=True, text=True).stdout.strip()
        problems += not path
        print(f"{_ok(bool(path))} {tool}: {version or 'not found'}")

    for mod in ("anthropic", "apify_client", "httpx", "yaml", "dotenv", "fastapi", "uvicorn", "typer"):
        try:
            importlib.import_module(mod)
            print(f"OK  import {mod}")
        except ImportError:
            problems += 1
            print(f"--  import {mod} FAILED (run `uv sync`)")

    try:
        cfg = C.config()
        name, col = C.active_collector()
        print(f"OK  config.yaml: {len(cfg['subreddits'])} subreddits, collector={name} ({col['actor_id']})")
    except Exception as e:  # noqa: BLE001
        problems += 1
        print(f"--  config.yaml failed to load: {e}")

    for folder in (C.RAW, C.THREADS, C.IMAGES, C.BOOKS, C.MASTER, C.IDEAS, C.GAMES, C.INBOX, C.CHATS):
        exists = folder.is_dir()
        problems += not exists
        print(f"{_ok(exists)} folder {folder.relative_to(C.ROOT)}/")

    env_exists = (C.ROOT / ".env").exists()
    print(f"{_ok(env_exists)} .env file {'present' if env_exists else 'missing (copy .env.example to .env when a key is needed)'}")
    for name in C.SECRET_NAMES:
        print(f"{_ok(bool(C.secret(name)))} {name} {'set' if C.secret(name) else 'not set yet'}")

    print("\nAll required checks passed." if problems == 0 else f"\n{problems} problem(s) found.")
    raise typer.Exit(code=1 if problems else 0)


@app.command("apify-check")
def apify_check() -> None:
    """Confirm the Apify token works (prints plan only, never the token)."""
    from collectors.apify_common import client
    user = client().user().get()
    plan = getattr(getattr(user, "plan", None), "id", None) if user else None
    print("Apify token: valid" if user else "Apify token: INVALID")
    if plan:
        print(f"Apify plan: {plan}")


@app.command("test-pull")
def test_pull(
    sub: str = typer.Option("homemadeTCGs", help="Subreddit to sample"),
    posts: int = typer.Option(10, help="Top posts to fetch"),
    comments: int = typer.Option(5, help="Comments per post"),
    timeframe: str = typer.Option("year"),
    collector: str = typer.Option(None, help="Collector name (default: active one in config)"),
) -> None:
    """Tiny paid sample run to inspect the actor's raw output."""
    from collections import Counter

    from collectors import get_collector
    from collectors.apify_common import load_raw_run

    from . import costs

    col = get_collector(collector)
    cfg = C.config()["collectors"][col.name]
    results = col.estimate_results(posts, comments)
    est = costs.Estimate(f"test pull r/{sub} via {cfg['actor_id']}")
    est.add(f"{posts} posts x (1 + {comments} comments), max_items cap", f"{results} results",
            costs.apify_cost(results, cfg))
    if not costs.confirm(est):
        print("Cancelled. Nothing was spent.")
        raise typer.Exit()

    run_id = costs.new_run_id("phase1-test")
    res = col.sample_threads(sub, timeframe, posts, comments)
    costs.log(run_id, "phase1", "apify", cfg["actor_id"], f"test pull r/{sub}",
              f"{res.raw_count} results", est.total, res.actual_usd, external_id=res.run_id)

    raw = load_raw_run(res.run_id)["items"]
    kinds = Counter(i.get("kind") or i.get("dataType") for i in raw)
    print(f"\nRun {res.run_id}: {res.raw_count} raw items {dict(kinds)}; "
          f"actual charge reported: {res.actual_usd if res.actual_usd is not None else 'n/a'}")
    print(f"Raw JSON saved under data/raw/_runs/{res.run_id}.json")
    for t in res.items:
        p = t.post
        depths = Counter(c.depth for c in t.comments)
        scores = [c.score for c in t.comments]
        print(f"\n- [{p.upvotes} up / {p.num_comments} cmts] {p.title[:80]}")
        print(f"  type={p.post_type} flair={p.flair!r} images={len(p.image_urls)} created={p.created_utc}")
        print(f"  {len(t.comments)} comments, depths={dict(depths)}, scores in order={scores}")


@app.command("collect")
def collect(
    sub: str = typer.Option(..., help="Subreddit name (without r/)"),
    refresh_candidates: bool = typer.Option(False, help="Re-buy the Top listings instead of reusing cached candidates"),
) -> None:
    """Phase 2: Top candidates -> rank -> comments for the winners. Two y/N gates."""
    from pipeline.fetch import collect as run
    run(sub, refresh_candidates=refresh_candidates)


@app.command("recover-run")
def recover_run(
    run_id: str = typer.Argument(..., help="Apify run ID (from the Apify Console)"),
    phase: str = typer.Option("phase2", help="Phase to log the cost under"),
) -> None:
    """Import a finished, already-paid Apify run from its dataset (free) so it is never re-bought."""
    from collectors.apify_common import client, save_run
    from lib import costs

    run = client().run(run_id).get()
    if run is None:
        print(f"No Apify run {run_id}.")
        raise typer.Exit(1)
    actor_id = next((c["actor_id"] for c in C.config()["collectors"].values()
                     if client().actor(c["actor_id"]).get().id == run.act_id), None)
    if actor_id is None:
        print("That run is not from a configured actor.")
        raise typer.Exit(1)
    rec = client().key_value_store(run.default_key_value_store_id).get_record("INPUT")
    run_input = (rec or {}).get("value") if isinstance(rec, dict) else getattr(rec, "value", None)
    run_input = run_input or {}

    # Rebuild the label the collector would have used, so the run is found and reused.
    if run_input.get("subredditName") and not run_input.get("scrapeComments"):
        label = f"list_top {run_input['subredditName']} {run_input.get('subredditTimeframe', 'all')}"
    elif run_input.get("startUrls") and not run_input.get("crawlCommentsPerPost"):
        url = run_input["startUrls"][0]["url"]
        tf = url.split("t=")[-1] if "t=" in url else "all"
        label = f"list_top {url.split('/r/')[1].split('/')[0]} {tf}"
    else:
        label = f"recovered {run_id}"

    res = save_run(run_id, actor_id, label, run_input, None)
    est = res.actual_usd or 0.0
    costs.log(costs.new_run_id(f"{phase}-recovered"), phase, "apify", actor_id,
              f"recovered run {run_id} ({label})", f"{res.raw_count} results", est, res.actual_usd,
              external_id=run_id)
    print(f"Saved {res.raw_count} items from run {run_id} as '{label}' (charged ${res.actual_usd or 0:.4f} originally; "
          f"logged to costs.csv). Recovery itself was free.")


@app.command("costs-reconcile")
def costs_reconcile() -> None:
    """Re-read every logged Apify run's final charge and correct costs.csv (free)."""
    import csv

    from collectors.apify_common import _actual_usd, _charged_count, client, remaining_monthly_usd

    path = C.COSTS
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fixed = 0
    for r in rows:
        if r["service"] != "apify" or not r.get("external_id"):
            continue
        run = client().run(r["external_id"]).get()
        if run is None:
            continue
        real = _actual_usd(run)
        old = float(r["actual_usd"] or 0)
        if real is not None and abs(real - old) > 1e-6:
            print(f"{r['external_id']}  {r['description'][:45]:<45}  logged ${old:.4f} -> Apify ${real:.4f} "
                  f"({_charged_count(run)} charged results)")
            r["actual_usd"] = f"{real:.6f}"
            fixed += 1
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    apify_total = sum(float(r["actual_usd"] or 0) for r in rows if r["service"] == "apify")
    print(f"\n{fixed} row(s) corrected. Apify total in costs.csv: ${apify_total:.4f}")
    left = remaining_monthly_usd()
    if left is not None:
        print(f"Apify credit left this billing cycle: ${left:.4f}")


@app.command("anthropic-check")
def anthropic_check() -> None:
    """Confirm the Anthropic key works with one tiny Haiku message (prints cost, never the key)."""
    import anthropic

    from . import claude
    try:
        text, cost, _ = claude.call("extract", [{"role": "user", "content": "Reply with the single word: ok"}],
                                    max_tokens=200)
        print(f"Anthropic key: valid (reply: {text.strip()!r}, cost ${cost:.6f})")
        from . import costs
        costs.log(costs.new_run_id("keycheck"), "setup", "anthropic", claude.model_for("extract"),
                  "Anthropic key check", "1 call", cost, cost)
    except anthropic.AuthenticationError:
        print("Anthropic key: INVALID")
    except anthropic.BadRequestError as e:
        print(f"Anthropic key: accepted, but the request failed: {e.message}")


@app.command("images")
def images(
    sub: str = typer.Option(..., help="Subreddit name"),
    model: str = typer.Option(None, help="opus / sonnet / haiku or a model ID (default: models.images)"),
    ids: str = typer.Option(None, help="Comma-separated post IDs to (re)describe; only these are paid for"),
) -> None:
    """Phase 3: select image posts, download (free), describe with Claude (y/N)."""
    from pipeline.describe_images import run
    run(sub, model=model, ids=[i.strip() for i in ids.split(",") if i.strip()] if ids else None)


@app.command("image-notes")
def image_notes(
    sub: str = typer.Option(..., help="Subreddit name"),
    flair: str = typer.Option(None, help="Only posts with this flair"),
    n: int = typer.Option(4, help="How many posts to show"),
    ids: str = typer.Option(None, help="Comma-separated post IDs instead of picking"),
) -> None:
    """Print image descriptions next to their file paths, for spot-checking (free)."""
    from lib.ledger import Ledger
    from lib.store import read_json
    from pipeline.describe_images import load_raw, notes_path

    led = Ledger()
    if ids:
        pick = [i.strip() for i in ids.split(",") if i.strip()]
    else:
        pick = [pid for pid, e in sorted(led.threads.items(), key=lambda kv: -(kv[1].get("engagement_score") or 0))
                if e.get("subreddit") == sub and e.get("images_done") == "described"
                and (not flair or (load_raw(sub, pid)["post"].get("flair") or "").lower() == flair.lower())][:n]
    for pid in pick:
        notes = read_json(notes_path(pid))
        post = load_raw(sub, pid)["post"]
        if not notes:
            print(f"\n=== {pid}: no image notes yet")
            continue
        print(f"\n=== {pid}  [{post.get('flair')}]  {post['title']}")
        print(f"    {post['url']}")
        print(f"    model: {notes['model']}  asked: {notes['what_is_asked']}")
        for img in notes["images"]:
            print(f"\n  data/images/{pid}/{img['file']}")
            for line in img["description"].splitlines() or [""]:
                print(f"    {line}")


@app.command("extract")
def extract(
    sub: str = typer.Option(..., help="Subreddit name"),
    ids: str = typer.Option(None, help="Comma-separated post IDs (direct calls, for examples/reruns)"),
    batch: bool = typer.Option(None, "--batch/--no-batch", help="Override config `batch` for this run"),
) -> None:
    """Phase 4: per-thread structured summaries -> data/threads/<id>.json (y/N)."""
    from pipeline.extract import run
    run(sub, ids=[i.strip() for i in ids.split(",") if i.strip()] if ids else None, use_batch=batch)


@app.command("extract-collect")
def extract_collect() -> None:
    """Finish any submitted extraction batches (if a run was interrupted)."""
    from lib.store import read_json
    from pipeline.extract import collect_batch
    jobs = read_json(C.DATA / "pending_batches.json", default={})
    if not jobs:
        print("No pending batches.")
    for batch_id in list(jobs):
        collect_batch(batch_id)


@app.command("show-thread")
def show_thread(post_id: str = typer.Argument(...)) -> None:
    """Print a thread's structured summary JSON (free)."""
    import json as _json
    from lib.store import read_json
    data = read_json(C.THREADS / f"{post_id}.json")
    print(_json.dumps(data, indent=2, ensure_ascii=False) if data else f"No summary for {post_id} yet.")


@app.command("ranked")
def ranked(sub: str = typer.Option(..., help="Subreddit name")) -> None:
    """Re-print the ranked list from the last collect (free)."""
    from lib.store import read_json
    from pipeline.fetch import ranked_path, ranked_table
    data = read_json(ranked_path(sub))
    if not data:
        print(f"No ranking yet for r/{sub}. Run `rl collect --sub {sub}`.")
        raise typer.Exit(1)
    print(ranked_table(data["kept"]))


if __name__ == "__main__":
    app()
