from datetime import datetime, timezone

from collectors.apify_fatihtahta import FatihtahtaCollector
from collectors.base import Post
from pipeline import rank as R
from pipeline.fetch import comment_results


def _post(pid, up, cm, created="2026-06-01T00:00:00Z"):
    return Post(id=pid, subreddit="x", title=pid, url=f"https://www.reddit.com/r/x/comments/{pid}/t/",
                created_utc=created, upvotes=up, num_comments=cm)


def test_engagement_weights_comments_more():
    w = {"upvote_weight": 1.0, "comment_weight": 1.5}
    # A debate (few upvotes, many comments) beats a pretty picture (many upvotes, few comments).
    assert R.engagement(50, 200, w) > R.engagement(400, 5, w)


def test_filter_recent_and_dedupe():
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    posts = [_post("a", 10, 1, "2026-01-01T00:00:00Z"), _post("b", 10, 1, "2024-01-01T00:00:00Z"),
             _post("a", 12, 3, "2026-01-01T00:00:00Z")]
    recent = R.filter_recent(R.dedupe(posts), 24, now)
    assert [p.id for p in recent] == ["a"]
    assert recent[0].upvotes == 12


def test_rank_order():
    ranked = R.rank([_post("low", 1, 0), _post("high", 100, 100)])
    assert [p.id for p, _ in ranked] == ["high", "low"]


def test_comment_results_caps_at_thread_size():
    assert comment_results([_post("a", 1, 5), _post("b", 1, 100)], 30) == (1 + 5) + (1 + 30)


def test_fatihtahta_mapping_keeps_comment_order():
    items = [
        {"kind": "post", "id": "t3_abc", "title": "T", "permalink": "/r/homemadeTCGs/comments/abc/t/",
         "subreddit": "homemadeTCGs", "created_utc": "2026-05-01T00:00:00Z", "score": 42,
         "num_comments": 3, "flair": "Feedback", "body": "text", "media_type": "gallery",
         "gallery_images": [{"url": "https://i.redd.it/x.png"}], "is_self": False},
        {"kind": "comment", "id": "t1_c1", "postId": "t3_abc", "parentId": "t3_abc", "depth": 0, "score": 5, "body": "first"},
        {"kind": "comment", "id": "t1_c2", "postId": "t3_abc", "parentId": "t1_c1", "depth": 1, "score": 9, "body": "reply"},
        {"kind": "comment", "id": "t1_c3", "postId": "t3_abc", "parentId": "t3_abc", "depth": 0, "score": 20, "body": "second"},
    ]
    [t] = FatihtahtaCollector.to_threads(FatihtahtaCollector.__new__(FatihtahtaCollector), items)
    assert t.post.id == "abc" and t.post.upvotes == 42 and t.post.image_urls == ["https://i.redd.it/x.png"]
    assert t.post.url == "https://www.reddit.com/r/homemadeTCGs/comments/abc/t/"
    assert [c.id for c in t.comments] == ["c1", "c2", "c3"]   # Best order kept, not re-sorted
    assert t.comments[0].parent_id is None and t.comments[1].parent_id == "c1"


def test_video_posts_drop_media():
    p = FatihtahtaCollector.to_post({"id": "t3_v", "permalink": "/r/x/comments/v/t/", "media_type": "video",
                                     "is_video": True, "url": "https://i.redd.it/x.png"})
    assert p.is_video and p.image_urls == []
