from app import chat


def test_history_is_text_only_and_summary_is_appended_not_edited():
    data = {"messages": [
        {"role": "user", "text": "old q"}, {"role": "assistant", "text": "old a"},
        {"role": "user", "text": "new q"}, {"role": "assistant", "text": "new a"}],
        "summary": {"text": "they asked old q", "covers_until": 2}}
    h = chat._history(data)
    assert [m["role"] for m in h] == ["user", "system", "assistant", "user", "assistant"]
    assert h[1]["content"].endswith("they asked old q")
    assert all(isinstance(m["content"], str) for m in h)        # no thinking blocks replayed


def test_history_without_summary_skips_empty_turns():
    data = {"messages": [{"role": "user", "text": "q"}, {"role": "assistant", "text": ""}], "summary": None}
    assert chat._history(data) == [{"role": "user", "content": "q"}]


def test_tab_validation():
    assert chat.tab_kind("book:homemadeTCGs") == ("book", "homemadeTCGs")
    for bad in ("master", "ideas", "book:../x", "game:"):
        try:
            chat.tab_kind(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_tools_reject_traversal_and_bad_input():
    assert chat.run_tool("read_chapter", {"book": "../etc", "chapter": "x"}) == ("Invalid book or chapter id.", False)
    assert chat.run_tool("read_thread", {})[1] is True
