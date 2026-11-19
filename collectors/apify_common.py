"""Shared Apify plumbing: run an actor under hard caps, read its dataset, report actual spend.

Every raw dataset is also saved to data/raw/_runs/<run_id>.json (gitignored), so a mapping bug
downstream never forces a second paid run.
"""

from __future__ import annotations

import re
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from apify_client import ApifyClient

from lib import config as C
from lib.store import write_json

from .base import RunResult

_IMG_EXT = re.compile(r"\.(png|jpe?g|webp|gif)(\?|$)", re.I)


def client() -> ApifyClient:
    token = C.secret("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN is not set in .env")
    return ApifyClient(token)


def run_actor(actor_id: str, run_input: dict, max_items: int, label: str) -> RunResult:
    """Run an actor synchronously with a hard result cap and a hard USD cap."""
    cfg = C.config()["apify"]
    # Hard USD cap: what max_items can cost (+5% and 1 cent slack), never above the config ceiling.
    col = next((c for c in C.config()["collectors"].values() if c["actor_id"] == actor_id), None)
    cap_usd = cfg["max_charge_usd_per_run"]
    if col:
        cap_usd = min(cap_usd, max_items * col["price_per_1000_results"] / 1000 * 1.05
                      + col.get("start_fee_usd", 0) + 0.01)
    run = client().actor(actor_id).call(
        run_input=run_input,
        max_items=max_items,
        max_total_charge_usd=Decimal(f"{cap_usd:.4f}"),
        run_timeout=timedelta(seconds=cfg["run_timeout_secs"]),
        logger=None,
    )
    if run is None:
        raise RuntimeError(f"Apify run for {actor_id} returned nothing")
    items = list(client().dataset(run.default_dataset_id).iterate_items())
    run = _settled_run(run.id, len(items))
    actual = _actual_usd(run)
    raw_path = C.RAW / "_runs" / f"{run.id}.json"
    write_json(raw_path, {
        "label": label, "actor_id": actor_id, "run_id": run.id, "status": str(run.status),
        "input": run_input, "max_items": max_items, "actual_usd": actual,
        "charged_event_counts": _dump(run.charged_event_counts), "items": items,
    })
    status = str(run.status).split(".")[-1].upper()
    if "SUCCEEDED" not in status:
        print(f"WARNING: Apify run {run.id} finished with status {status} "
              f"({run.status_message}). {len(items)} items kept; raw saved to {raw_path.relative_to(C.ROOT)}")
    return RunResult(items=items, actual_usd=actual, run_id=run.id, raw_count=len(items))


def _dump(obj):
    if obj is None:
        return None
    return obj.model_dump(mode="json", by_alias=True) if hasattr(obj, "model_dump") else obj


def _charged_count(run) -> int:
    counts = _dump(run.charged_event_counts) or {}
    return int(sum(counts.values())) if isinstance(counts, dict) else 0


def _settled_run(run_id: str, item_count: int, max_wait_secs: int = 60):
    """Apify finishes counting per-result charges a few seconds after a run ends.

    Reading the run straight away under-reports spend (the Phase 1 test showed $0.019
    when the real charge was $0.0894). Re-read until the charged count covers every
    dataset item, or stops changing.
    """
    rc = client().run(run_id)
    run = rc.get()
    last, stable = -1, 0
    deadline = time.monotonic() + max_wait_secs
    while time.monotonic() < deadline:
        n = _charged_count(run)
        if n >= item_count:
            break
        stable = stable + 1 if n == last else 0
        if stable >= 3:
            break
        last = n
        time.sleep(3)
        run = rc.get()
    return run


def _actual_usd(run) -> float | None:
    """What the run charged: sum of per-event usage, else Apify's usage total."""
    usage = _dump(getattr(run, "event_usage", None))
    if isinstance(usage, dict) and usage:
        return float(sum((v or {}).get("eventTotalUsd") or 0 for v in usage.values()))
    val = getattr(run, "usage_total_usd", None)
    return float(val) if val is not None else None


def remaining_monthly_usd() -> float | None:
    """Apify credit left in this billing cycle (None if it can't be read)."""
    try:
        lim = _dump(client().user().limits())
        return float(lim["limits"]["maxMonthlyUsageUsd"]) - float(lim["current"]["monthlyUsageUsd"])
    except Exception:  # noqa: BLE001
        return None


def load_raw_run(run_id: str) -> dict:
    from lib.store import read_json
    return read_json(C.RAW / "_runs" / f"{run_id}.json")


# --- normalization helpers shared by adapters -------------------------------

def strip_prefix(reddit_id: str | None) -> str | None:
    if not reddit_id:
        return None
    return reddit_id.split("_", 1)[1] if reddit_id[:3] in ("t1_", "t3_") else reddit_id


def absolute_url(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith("http") else "https://www.reddit.com" + url


def is_image_url(url: str | None) -> bool:
    return bool(url) and ("i.redd.it" in url or "preview.redd.it" in url or "i.imgur.com" in url
                          or bool(_IMG_EXT.search(url)))


def post_id_from_url(url: str) -> str | None:
    m = re.search(r"/comments/([a-z0-9]+)", url)
    return m.group(1) if m else None


def subreddit_from_url(url: str) -> str | None:
    m = re.search(r"/r/([A-Za-z0-9_]+)", url)
    return m.group(1) if m else None


def dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def raw_dir(subreddit: str) -> Path:
    return C.RAW / subreddit
