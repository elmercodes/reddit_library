"""Adapter for fatihtahta/reddit-scraper-search-fast. All of this actor's field names live here."""

from __future__ import annotations

import html

from lib import config as C

from . import apify_common as A
from .base import Collector, Comment, Post, RunResult, Thread


class FatihtahtaCollector(Collector):
    name = "fatihtahta"

    def __init__(self):
        self.cfg = C.config()["collectors"][self.name]
        self.nsfw = C.config()["collection"].get("include_nsfw", False)

    # --- interface ------------------------------------------------------
    def list_top(self, subreddit: str, timeframe: str, limit: int, since: str | None = None) -> RunResult:
        run_input = {
            "subredditName": subreddit,
            "subredditSort": "top",
            "subredditTimeframe": timeframe,
            "maxPosts": limit,
            "scrapeComments": False,
            "includeNsfw": self.nsfw,
        }
        if since:
            # Record-level filter: older posts are not saved, so they are not billed.
            # Ranking stays "top" (forceSortNewForTimeFilteredRuns stays off).
            run_input["dateFrom"] = since
        res = A.run_actor(self.cfg["actor_id"], run_input, max_items=limit,
                          label=f"list_top {subreddit} {timeframe}")
        res.items = [self.to_post(i) for i in res.items if i.get("kind", "post") == "post"]
        return res

    def sample_threads(self, subreddit: str, timeframe: str, posts: int, comments: int) -> RunResult:
        """Top posts with comments in one run (used for the Phase 1 test pull)."""
        run_input = {
            "subredditName": subreddit, "subredditSort": "top", "subredditTimeframe": timeframe,
            "maxPosts": posts, "scrapeComments": True, "maxComments": comments,
            "includeNsfw": self.nsfw,
        }
        res = A.run_actor(self.cfg["actor_id"], run_input,
                          max_items=self.estimate_results(posts, comments),
                          label=f"sample {subreddit} {timeframe}")
        res.items = self.to_threads(res.items)
        return res

    def fetch_threads(self, urls: list[str], comments_per_thread: int) -> RunResult:
        run_input = {
            "urls": urls,
            "maxPosts": 1,
            "scrapeComments": comments_per_thread > 0,
            "maxComments": max(comments_per_thread, 1),
            "includeNsfw": True,  # explicit links are always wanted
        }
        cap = self.estimate_results(len(urls), comments_per_thread)
        res = A.run_actor(self.cfg["actor_id"], run_input, max_items=cap,
                          label=f"fetch_threads {len(urls)} urls")
        res.items = self.to_threads(res.items)
        return res

    def estimate_results(self, posts: int, comments_per_post: int = 0) -> int:
        billed_comments = comments_per_post if self.cfg.get("comments_billed", True) else 0
        return posts * (1 + billed_comments)

    # --- mapping ----------------------------------------------------------
    def to_threads(self, items: list[dict]) -> list[Thread]:
        posts = {}
        comments: dict[str, list[Comment]] = {}
        for i in items:
            if i.get("kind") == "comment":
                c = self.to_comment(i)
                pid = A.strip_prefix(i.get("postId")) or A.post_id_from_url(i.get("postUrl") or i.get("url") or "")
                comments.setdefault(pid, []).append(c)
            else:
                p = self.to_post(i)
                posts[p.id] = p
        return [Thread(post=p, comments=comments.get(pid, [])) for pid, p in posts.items()]

    @staticmethod
    def to_post(i: dict) -> Post:
        images = []
        for g in i.get("gallery_images") or []:
            images.append(g.get("url") if isinstance(g, dict) else g)
        for m in i.get("media_assets") or []:
            if isinstance(m, dict) and (m.get("type") in (None, "image", "gif") ):
                images.append(m.get("original_url") or m.get("url"))
        dest = i.get("url_overridden_by_dest") or i.get("url")
        if A.is_image_url(dest):
            images.append(dest)
        images = [html.unescape(u) for u in A.dedupe(images) if A.is_image_url(u)]

        media_type = i.get("media_type") or ("text" if i.get("is_self") else "link")
        is_video = bool(i.get("is_video")) or media_type == "video"
        if is_video:
            images = []  # skip video entirely; keep text + comments
        permalink = A.absolute_url(i.get("permalink")) or A.absolute_url(i.get("url"))
        pid = A.strip_prefix(i.get("id")) or A.post_id_from_url(permalink or "")
        return Post(
            id=pid,
            subreddit=(i.get("subreddit") or A.subreddit_from_url(permalink or "") or "").removeprefix("r/"),
            title=i.get("title") or "",
            url=permalink,
            created_utc=i.get("created_utc") or "",
            upvotes=int(i.get("score") or 0),
            num_comments=int(i.get("num_comments") or 0),
            flair=i.get("flair") or None,
            selftext=i.get("body") or "",
            post_type=media_type,
            is_video=is_video,
            image_urls=images,
            link_url=None if i.get("is_self") else dest,
            nsfw=bool(i.get("over_18")),
        )

    @staticmethod
    def to_comment(i: dict) -> Comment:
        parent = i.get("parentId") or ""
        return Comment(
            id=A.strip_prefix(i.get("id")),
            parent_id=None if parent.startswith("t3_") or not parent else A.strip_prefix(parent),
            depth=int(i.get("depth") or 0),
            score=int(i.get("score") or 0),
            body=i.get("body") or "",
            created_utc=i.get("created_utc"),
        )
