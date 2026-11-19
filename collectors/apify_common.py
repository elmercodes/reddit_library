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


def find_saved_run(actor_id: str, label: str, max_age_days: int) -> dict | None:
    """Most recent saved raw run with this actor + label, if younger than max_age_days."""
    from datetime import datetime, timezone

    from lib.store import read_json
    best = None
    for f in (C.RAW / "_runs").glob("*.json"):
        d = read_json(f)
        if not d or d.get("actor_id") != actor_id or d.get("label") != label:
            continue
        saved = d.get("saved_at")
        if not saved:
            continue
        age = datetime.now(timezone.utc) - datetime.fromisoformat(saved)
        if age.days <= max_age_days and (best is None or saved > best["saved_at"]):
            best = d
    return best


def run_actor(actor_id: str, run_input: dict, max_items: int, label: str,
              reuse_days: int | None = None) -> RunResult:
    """Run an actor synchronously with a hard result cap and a hard USD cap.

    If `reuse_days` is set and a run with the same actor + label was saved within that many
    days, its saved items are returned at no charge instead of buying them again.
    """
    if reuse_days is not None:
        saved = find_saved_run(actor_id, label, reuse_days)
        if saved:
            print(f"Reusing saved Apify run {saved['run_id']} for '{label}' from {saved['saved_at'][:10]} (no charge).")
            return RunResult(items=saved["items"], actual_usd=0.0, run_id=saved["run_id"],
                             raw_count=len(saved["items"]), reused=True)
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
    return save_run(run.id, actor_id, label, run_input, max_items)


def save_run(run_id: str, actor_id: str, label: str, run_input: dict, max_items: int | None) -> RunResult:
    """Read a finished run's dataset and save it locally FIRST, then work out the charge.

    Reading a dataset is free, so this is also how a paid run is recovered after a crash.
    """
    from lib.ledger import now
    run = client().run(run_id).get()
    items = list(client().dataset(run.default_dataset_id).iterate_items())
    raw_path = C.RAW / "_runs" / f"{run_id}.json"
    record = {
        "label": label, "actor_id": actor_id, "run_id": run_id, "saved_at": now(),
        "status": str(run.status), "input": run_input, "max_items": max_items,
        "actual_usd": None, "charged_event_counts": None, "items": items,
    }
    write_json(raw_path, record)  # paid data is on disk before anything else can fail

    actual = None
    try:
        run = _settled_run(run_id, len(items))
        actual = _actual_usd(run)
        source = "apify"
        if _charged_count(run) < len(items):
            # Apify still hasn't finished counting: log what the items cost at list price
            # (flagged), and `rl costs-reconcile` replaces it with Apify's final figure later.
            col = _price_for(actor_id)
            if col:
                actual = max(actual or 0.0, len(items) * col["price_per_1000_results"] / 1000)
                source = "provisional"
                print(f"NOTE: Apify had not finished counting charges for run {run_id}; logged "
                      f"${actual:.4f} from item count. Run `rl costs-reconcile` later to confirm.")
        record["actual_usd"] = actual
        record["actual_source"] = source
        record["charged_event_counts"] = _dump(run.charged_event_counts)
        write_json(raw_path, record)
    except Exception as e:  # noqa: BLE001 - cost reporting must never lose data
        print(f"WARNING: could not read the charge for run {run_id} ({e}); check the Apify Console.")

    status = str(run.status).split(".")[-1].upper()
    if "SUCCEEDED" not in status:
        print(f"WARNING: Apify run {run_id} finished with status {status} "
              f"({run.status_message}). {len(items)} items kept; raw saved to {raw_path.relative_to(C.ROOT)}")
    return RunResult(items=items, actual_usd=actual, run_id=run_id, raw_count=len(items))


def _dump(obj):
    """Pydantic models (also nested inside dicts/lists) -> plain JSON-able data."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True)
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dump(v) for v in obj]
    return obj


def _charged_count(run) -> int:
    counts = _dump(run.charged_event_counts) or {}
    return int(sum(counts.values())) if isinstance(counts, dict) else 0


def _settled_run(run_id: str, item_count: int, max_wait_secs: int = 180):
    """Apify finishes counting per-result charges well after a run ends (seconds to minutes).

    Reading too early under-reports spend: the Phase 1 test showed $0.019 for a $0.0894 run,
    and stopping when the count merely paused under-logged Phase 2 chunks by $0.44. So wait
    until every dataset item has been charged, with no early exit; the caller handles a timeout.
    """
    rc = client().run(run_id)
    run = rc.get()
    deadline = time.monotonic() + max_wait_secs
    while _charged_count(run) < item_count and time.monotonic() < deadline:
        time.sleep(5)
        run = rc.get()
    return run


def _price_for(actor_id: str) -> dict | None:
    return next((c for c in C.config()["collectors"].values() if c["actor_id"] == actor_id), None)


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
