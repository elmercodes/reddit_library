"""Adapter for harshmaur/reddit-scraper (backup collector). All of this actor's field names live here.

Field names are from the actor's README; they are confirmed against a live run the first time
this collector is used (optional test in Phase 1).
"""

from __future__ import annotations

import html

from lib import config as C

from . import apify_common as A
from .base import Collector, Comment, Post, RunResult, Thread


class HarshmaurCollector(Collector):
    name = "harshmaur"

    def __init__(self):
        self.cfg = C.config()["collectors"][self.name]
        self.nsfw = C.config()["collection"].get("include_nsfw", False)

    def _base_input(self) -> dict:
        # Never enable the paid AI add-ons.
        return {"aiAnalysis": False, "searchTerms": [], "subredditUrls": []}

    def list_top(self, subreddit: str, timeframe: str, limit: int, since: str | None = None,
                 reuse_days: int | None = None) -> RunResult:
        # `since` is ignored here: this actor's postedAfter switches to newest-first,
        # which breaks Top ranking. The 24-month filter is applied locally instead.
        run_input = self._base_input() | {
            "startUrls": [{"url": f"https://www.reddit.com/r/{subreddit}/top/?t={timeframe}"}],
            "maxPostsCount": limit,
            "crawlCommentsPerPost": False,
            "includeNSFW": self.nsfw,
        }
        res = A.run_actor(self.cfg["actor_id"], run_input, max_items=limit,
                          label=f"list_top {subreddit} {timeframe}", reuse_days=reuse_days)
        res.items = [self.to_post(i) for i in res.items if i.get("dataType", "post") == "post"]
        return res

    def sample_threads(self, subreddit: str, timeframe: str, posts: int, comments: int) -> RunResult:
        """Top posts with comments in one run (used for the Phase 1 test pull)."""
        run_input = self._base_input() | {
            "startUrls": [{"url": f"https://www.reddit.com/r/{subreddit}/top/?t={timeframe}"}],
            "maxPostsCount": posts, "crawlCommentsPerPost": True,
            "maxCommentsPerPost": comments, "includeNSFW": self.nsfw,
        }
        res = A.run_actor(self.cfg["actor_id"], run_input,
                          max_items=self.estimate_results(posts, comments),
                          label=f"sample {subreddit} {timeframe}")
        res.items = self.to_threads(res.items)
        return res

    def fetch_threads(self, urls: list[str], comments_per_thread: int) -> RunResult:
        run_input = self._base_input() | {
            "startUrls": [{"url": u} for u in urls],
            "maxPostsCount": 1,
            "crawlCommentsPerPost": comments_per_thread > 0,
            "maxCommentsPerPost": max(comments_per_thread, 1),
            "includeNSFW": True,
        }
        cap = self.estimate_results(len(urls), comments_per_thread)
        res = A.run_actor(self.cfg["actor_id"], run_input, max_items=cap,
                          label=f"fetch_threads {len(urls)} urls")
        res.items = self.to_threads(res.items)
        return res

    def estimate_results(self, posts: int, comments_per_post: int = 0) -> int:
        billed_comments = comments_per_post if self.cfg.get("comments_billed", True) else 0
        return posts * (1 + billed_comments)

    def to_threads(self, items: list[dict]) -> list[Thread]:
        posts, comments = {}, {}
        for i in items:
            kind = i.get("dataType")
            if kind == "comment":
                pid = A.strip_prefix(i.get("postId")) or A.post_id_from_url(i.get("url") or "")
                comments.setdefault(pid, []).append(self.to_comment(i))
            elif kind in (None, "post"):
                p = self.to_post(i)
                posts[p.id] = p
        return [Thread(post=p, comments=comments.get(pid, [])) for pid, p in posts.items()]

    @staticmethod
    def to_post(i: dict) -> Post:
        url = A.absolute_url(i.get("url") or i.get("postUrl"))
        is_video = bool(i.get("isVideo"))
        images = [] if is_video else [html.unescape(u) for u in A.dedupe(i.get("images") or [])
                                      if isinstance(u, str) and A.is_image_url(u)]
        link = i.get("link") or i.get("externalUrl")
        if not is_video and A.is_image_url(link):
            images = A.dedupe(images + [link])
        if is_video:
            ptype = "video"
        elif len(images) > 1:
            ptype = "gallery"
        elif images:
            ptype = "image"
        elif link and "reddit.com" not in link:
            ptype = "link"
        else:
            ptype = "text"
        pid = A.strip_prefix(i.get("parsedId") or i.get("id")) or A.post_id_from_url(url or "")
        return Post(
            id=pid,
            subreddit=(i.get("parsedCommunityName") or i.get("communityName") or
                       A.subreddit_from_url(url or "") or "").removeprefix("r/"),
            title=i.get("title") or "",
            url=url,
            created_utc=i.get("createdAt") or "",
            upvotes=int(i.get("upVotes") or i.get("score") or 0),
            num_comments=int(i.get("commentsCount") or i.get("numberOfComments") or 0),
            flair=i.get("flair") or None,
            selftext=i.get("body") or "",
            post_type=ptype,
            is_video=is_video,
            image_urls=images,
            link_url=link,
            nsfw=bool(i.get("over18") or i.get("isNsfw")),
        )

    @staticmethod
    def to_comment(i: dict) -> Comment:
        parent = i.get("parentId") or ""
        return Comment(
            id=A.strip_prefix(i.get("parsedId") or i.get("id")),
            parent_id=None if parent.startswith("t3_") or not parent else A.strip_prefix(parent),
            depth=int(i.get("depth") or 0),
            score=int(i.get("commentUpVotes") or i.get("score") or i.get("upVotes") or 0),
            body=i.get("body") or "",
            created_utc=i.get("commentCreatedAt") or i.get("createdAt"),
        )
