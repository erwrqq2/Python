from findex.snippet import make_snippet


def test_highlights_terms_case_insensitively():
    assert make_snippet("The Whale swam.", ["whale"]) == "The **Whale** swam."


def test_window_is_trimmed_with_ellipsis():
    text = "x " * 100 + "whale" + " y" * 100
    snippet = make_snippet(text, ["whale"], width=10)
    assert snippet.startswith("…") and snippet.endswith("…")
    assert "**whale**" in snippet
    assert len(snippet) < 40


def test_best_window_has_most_distinct_terms():
    text = "whale " + "filler " * 50 + "the whale met a shark " + "filler " * 50
    snippet = make_snippet(text, ["whale", "shark"], width=20)
    assert "**whale** met a **shark**" in snippet


def test_whole_words_only_and_apostrophes():
    assert make_snippet("whales and a whale", ["whale"]) == "whales and a **whale**"
    assert make_snippet("Don’t stop", ["don't"]) == "**Don’t** stop"


def test_no_terms_or_no_hits():
    assert make_snippet("text", []) == ""
    assert make_snippet("text", ["absent"]) == ""
