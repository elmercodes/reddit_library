"""Phase 2: collect candidates (posts only), rank by engagement, then fetch comments for winners.

Two paid steps, each behind its own y/N:
  1. Top listings (posts only) -> data/raw/<sub>/_candidates.json  (reused on later runs)
  2. Comments for the kept threads -> data/raw/<sub>/<id>.json     (ledger: fetched_at)
"""

from __future__ import annotations

from dataclasses import asdict

from collectors import get_collector
from collectors.apify_common import find_saved_run, remaining_monthly_usd
from collectors.base import Post
from lib import config as C
from lib import costs
from lib.ledger import Ledger, now
from lib.store import read_json, write_json

from . import rank as R


def _sub_dir(sub: str):
    return C.RAW / sub


def candidates_path(sub: str):
    return _sub_dir(sub) / "_candidates.json"


def ranked_path(sub: str):
    return _sub_dir(sub) / "_ranked.json"


def load_candidates(sub: str) -> list[Post] | None:
    data = read_json(candidates_path(sub))
    if not data:
        return None
    return [Post(**p) for p in data["posts"]]


def comment_results(posts: list[Post], per_thread: int) -> int:
    """Billable results to fetch these threads: 1 post + up to `per_thread` comments each."""
    return sum(1 + min(per_thread, max(p.num_comments, 0)) for p in posts)


def _credit_line(est: costs.Estimate) -> None:
    left = remaining_monthly_usd()
    if left is None:
        return
    print(f"Apify credit left this billing cycle: ${left:.4f}")
    if est.total > left:
        print(f"WARNING: this estimate (${est.total:.4f}) is more than the credit left. Apify will stop "
              f"the run when the credit runs out; finished chunks are kept and nothing is re-bought.")


def ranked_table(rows: list[dict], limit: int | None = None) -> str:
    lines = [f"{'#':>3}  {'score':>6}  {'upvotes':>7}  {'cmts':>5}  {'date':<10}  title"]
    for r in rows[:limit] if limit else rows:
        lines.append(f"{r['rank']:>3}  {r['engagement_score']:>6.2f}  {r['upvotes']:>7}  "
                     f"{r['num_comments']:>5}  {r['created_utc'][:10]:<10}  {r['title'][:70]}")
    return "\n".join(lines)


def collect(sub: str, refresh_candidates: bool = False) -> None:
    cfg = C.config()["collection"]
    col = get_collector()
    col_cfg = C.config()["collectors"][col.name]
    per_thread = cfg["comments_per_thread"]
    keep = cfg["keep_per_sub"]
    ledger = Ledger()

    cached = None if refresh_candidates else load_candidates(sub)

    # ---- Gate 1: candidates (+ upper-bound preview of step 2) ----------------
    est = costs.Estimate(f"Phase 2 r/{sub} via {col_cfg['actor_id']}")
    if cached is None:
        for tf in cfg["timeframes"]:
            n = cfg["posts_per_listing"]
            saved = None if refresh_candidates else find_saved_run(
                col_cfg["actor_id"], f"list_top {sub} {tf}", cfg.get("listing_reuse_days", 30))
            if saved:
                est.add(f"Step 1: Top/{tf} listing (saved run {saved['run_id']}, reused)",
                        f"{len(saved['items'])} results", 0.0)
            else:
                est.add(f"Step 1: Top/{tf} listing, posts only (max)", f"{n} results",
                        costs.apify_cost(n, col_cfg))
    else:
        print(f"Using {len(cached)} cached candidates from {candidates_path(sub).relative_to(C.ROOT)} (no charge).")
    if cached is not None:
        # Candidates are known, so the step 2 preview is exact: only kept threads not yet fetched.
        need = [p for p, _ in R.rank(cached)[:keep] if not ledger.has(p.id, "fetched_at")]
        if not need:
            print(f"All {keep} kept threads for r/{sub} are already fetched. Nothing to buy.")
            return
        n = comment_results(need, per_thread)
        est.add(f"Step 2 preview: {len(need)} kept thread(s) not yet fetched", f"{n} results",
                costs.apify_cost(n, col_cfg))
    else:
        upper = keep * (1 + per_thread)
        est.add(f"Step 2 preview: {keep} threads x (1 + {per_thread} comments), max",
                f"{upper} results", costs.apify_cost(upper, col_cfg))
    print("Step 2 is confirmed separately after ranking, with an exact estimate.")
    _credit_line(est)
    if not costs.confirm(est):
        print("Cancelled. Nothing was spent.")
        return

    if cached is None:
        since = R.cutoff(cfg["max_age_months"]).date().isoformat()
        posts: list[Post] = []
        for tf in cfg["timeframes"]:
            run_id = costs.new_run_id("phase2-candidates")
            n = cfg["posts_per_listing"]
            res = col.list_top(sub, tf, n, since=since,
                               reuse_days=None if refresh_candidates else cfg.get("listing_reuse_days", 30))
            if not res.reused:
                costs.log(run_id, "phase2", "apify", col_cfg["actor_id"], f"r/{sub} Top/{tf} candidates",
                          f"{res.raw_count} results", costs.apify_cost(n, col_cfg), res.actual_usd,
                          external_id=res.run_id)
            print(f"Top/{tf}: {len(res.items)} posts (charged ${res.actual_usd or 0:.4f}, Apify run {res.run_id})")
            posts.extend(res.items)
        fresh = R.filter_recent(R.dedupe(posts), cfg["max_age_months"])
        write_json(candidates_path(sub), {
            "subreddit": sub, "fetched_at": now(), "collector": col.name,
            "raw_posts": len(posts), "posts": [asdict(p) for p in fresh],
        })
        cached = fresh

    # ---- Rank ------------------------------------------------------------
    ranked = R.rank(cached)[: cfg["candidates_per_sub"]]
    kept = ranked[:keep]
    rows = [{"rank": i + 1, "id": p.id, "engagement_score": round(s, 4), "upvotes": p.upvotes,
             "num_comments": p.num_comments, "created_utc": p.created_utc, "title": p.title,
             "url": p.url, "post_type": p.post_type, "flair": p.flair}
            for i, (p, s) in enumerate(kept)]
    write_json(ranked_path(sub), {"subreddit": sub, "ranked_at": now(), "kept": rows,
                                  "candidates": len(cached)})
    print(f"\n{len(cached)} candidates after 24-month filter and de-duplication; keeping top {len(kept)}.\n")
    print(ranked_table(rows))

    # ---- Gate 2: comments for the kept threads -----------------------------
    to_fetch = [(p, s) for p, s in kept if not ledger.has(p.id, "fetched_at")]
    skipped = len(kept) - len(to_fetch)
    if not to_fetch:
        print(f"\nAll {len(kept)} kept threads are already in the ledger. Nothing to buy.")
        return
    results = comment_results([p for p, _ in to_fetch], per_thread)
    est2 = costs.Estimate(f"Phase 2 step 2: comments for r/{sub}")
    est2.add(f"{len(to_fetch)} threads x (1 + min({per_thread}, comment count))",
             f"{results} results", costs.apify_cost(results, col_cfg))
    if skipped:
        print(f"\n{skipped} kept threads already in the ledger are skipped.")
    _credit_line(est2)
    if not costs.confirm(est2):
        print("Cancelled before step 2. Candidates are saved and will be reused at no charge.")
        return

    _fetch_comments(sub, to_fetch, per_thread, col, col_cfg, ledger)


def _fetch_comments(sub, to_fetch, per_thread, col, col_cfg, ledger) -> None:
    chunk = C.config()["collection"].get("fetch_chunk_size", 25)
    got, missing, spent = 0, [], 0.0
    for start in range(0, len(to_fetch), chunk):
        part = to_fetch[start:start + chunk]
        run_id = costs.new_run_id("phase2-comments")
        est_usd = costs.apify_cost(comment_results([p for p, _ in part], per_thread), col_cfg)
        res = col.fetch_threads([p.url for p, _ in part], per_thread)
        by_id = {t.post.id: t for t in res.items}
        for p, score in part:
            t = by_id.get(p.id)
            if t is None:
                missing.append(p)
                continue
            write_json(_sub_dir(sub) / f"{p.id}.json", {
                "fetched_at": now(), "collector": col.name, "apify_run_id": res.run_id,
                "engagement_score": round(score, 4),
                "post": asdict(t.post), "comments": [asdict(c) for c in t.comments],
            })
            ledger.mark(p.id, fetched_at=now(), subreddit=sub, engagement_score=round(score, 4),
                        run_id=run_id, book=sub)
            got += 1
        ledger.save()
        spent += res.actual_usd or 0
        costs.log(run_id, "phase2", "apify", col_cfg["actor_id"],
                  f"r/{sub} comments chunk {start // chunk + 1}", f"{res.raw_count} results",
                  est_usd, res.actual_usd, external_id=res.run_id)
        print(f"Chunk {start // chunk + 1}: {len(by_id)} threads, {res.raw_count} results, "
              f"charged ${res.actual_usd or 0:.4f}")
    print(f"\nFetched {got} threads for r/{sub}; total charged ${spent:.4f}.")
    if missing:
        print(f"{len(missing)} threads came back empty and are NOT in the ledger (safe to retry):")
        for p in missing:
            print(f"  - {p.url}")
