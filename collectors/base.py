"""Collector interface. Every collector returns these normalized records,
so nothing downstream knows which Apify actor produced them."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field


@dataclass
class Comment:
    id: str
    parent_id: str | None     # None for top-level comments
    depth: int
    score: int
    body: str
    created_utc: str | None = None


@dataclass
class Post:
    id: str                   # Reddit base36 post ID, without the t3_ prefix
    subreddit: str
    title: str
    url: str                  # permalink to the Reddit thread
    created_utc: str          # ISO 8601
    upvotes: int
    num_comments: int
    flair: str | None = None
    selftext: str = ""
    post_type: str = "text"   # text | image | gallery | video | gif | link
    is_video: bool = False
    image_urls: list[str] = field(default_factory=list)
    link_url: str | None = None
    nsfw: bool = False


@dataclass
class Thread:
    post: Post
    comments: list[Comment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunResult:
    items: list
    actual_usd: float | None
    run_id: str | None
    raw_count: int


class Collector(ABC):
    name: str

    @abstractmethod
    def list_top(self, subreddit: str, timeframe: str, limit: int) -> RunResult:
        """Top posts (no comments) for a subreddit and time filter. items: list[Post]."""

    @abstractmethod
    def fetch_threads(self, urls: list[str], comments_per_thread: int) -> RunResult:
        """Posts plus up to `comments_per_thread` comment records each. items: list[Thread]."""

    @abstractmethod
    def estimate_results(self, posts: int, comments_per_post: int = 0) -> int:
        """Billable results for a run of this shape."""
