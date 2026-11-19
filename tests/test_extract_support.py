from pipeline import extract as X


def _raw():
    return {
        "post": {"id": "p1", "author": "op_user", "subreddit": "x", "title": "t", "flair": None,
                 "created_utc": "2026-01-01T00:00:00Z", "upvotes": 10, "num_comments": 5,
                 "post_type": "text", "selftext": "", "url": "u"},
        "comments": [
            {"id": "a", "parent_id": None, "depth": 0, "score": 40, "body": "ban it", "author": "u1"},
            {"id": "b", "parent_id": "a", "depth": 1, "score": 5, "body": "thanks", "author": "op_user"},
            {"id": "c", "parent_id": None, "depth": 0, "score": 30, "body": "agree, ban", "author": "u2"},
            {"id": "d", "parent_id": None, "depth": 0, "score": 2, "body": "lone take", "author": "u3"},
            {"id": "e", "parent_id": None, "depth": 0, "score": 8, "body": "no ban", "author": "u4"},
        ],
    }


def _fields(**over):
    base = {"topics": [], "summary": "", "consensus": [], "notable_points": [], "disagreements": [],
            "design_lessons": [], "business_lessons": [], "content_ideas": []}
    base.update(over)
    return base


def test_op_replies_never_count_and_totals_exclude_op():
    f = X.apply_support(_raw(), _fields(consensus=[{"point": "ban", "evidence": ["C1", "C2", "C3"]}]))
    assert f["comment_points_total"] == 80          # 40 + 30 + 2 + 8, OP's 5 excluded
    s = f["consensus"][0]["support"]
    assert s == {"comments": 2, "commenters": 2, "points": 70, "share": 0.875}


def test_single_commenter_consensus_is_demoted():
    f = X.apply_support(_raw(), _fields(consensus=[{"point": "lone", "evidence": ["C4"]}]))
    assert f["consensus"] == []
    assert f["notable_points"][0]["demoted_from_consensus"] is True


def test_split_without_evidence_on_one_side_is_dropped():
    f = X.apply_support(_raw(), _fields(disagreements=[
        {"issue": "ban?", "side_a": "yes", "side_a_evidence": ["C1", "C3"], "side_b": "no", "side_b_evidence": ["C5"]},
        {"issue": "fake", "side_a": "x", "side_a_evidence": ["C1"], "side_b": "y", "side_b_evidence": []},
    ]))
    assert [d["issue"] for d in f["disagreements"]] == ["ban?"]
    assert f["disagreements"][0]["side_b_support"]["points"] == 8
