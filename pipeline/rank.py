"""Engagement ranking for candidate posts."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from collectors.base import Post
from lib import config as C


def engagement(upvotes: int, comments: int, weights: dict | None = None) -> float:
    w = weights or C.config()["scoring"]
    return w["upvote_weight"] * math.log(max(upvotes, 0) + 1) + w["comment_weight"] * math.log(max(comments, 0) + 1)


def parse_time(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if isinstance(s, (int, float)) or s.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def cutoff(max_age_months: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=round(max_age_months * 30.44))


def filter_recent(posts: list[Post], max_age_months: int, now: datetime | None = None) -> list[Post]:
    floor = cutoff(max_age_months, now)
    return [p for p in posts if (t := parse_time(p.created_utc)) is not None and t >= floor]


def dedupe(posts: list[Post]) -> list[Post]:
    """Keep one copy per post ID (the one with the highest counts, in case listings differ)."""
    best: dict[str, Post] = {}
    for p in posts:
        cur = best.get(p.id)
        if cur is None or (p.upvotes, p.num_comments) > (cur.upvotes, cur.num_comments):
            best[p.id] = p
    return list(best.values())


def rank(posts: list[Post], weights: dict | None = None) -> list[tuple[Post, float]]:
    scored = [(p, engagement(p.upvotes, p.num_comments, weights)) for p in posts]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
