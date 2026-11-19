from pipeline.write import _link_refs


def test_link_refs_turns_citations_into_reddit_links():
    refmap = {"T1": {"url": "https://r/1"}, "S2": {"url": "https://r/s2"}}
    assert _link_refs("Big point [T1, S2].", refmap) == "Big point ([T1](https://r/1), [S2](https://r/s2))."


def test_link_refs_leaves_unknown_refs_alone():
    assert _link_refs("See [T9].", {"T1": {"url": "u"}}) == "See [T9]."
