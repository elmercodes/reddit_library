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
              f"{res.raw_count} results", est.total, res.actual_usd)

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
