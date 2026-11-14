"""Tokenizer checks (plain asserts; runnable with pytest)."""

import inspect

from findex.tokenize import tokenize


def toks(text: str) -> list[str]:
    return list(tokenize(text))


def test_is_generator() -> None:
    assert inspect.isgeneratorfunction(tokenize)
    assert inspect.isgenerator(tokenize("hello"))


def test_empty_and_punctuation_only() -> None:
    assert toks("") == []
    assert toks("... !!! -- ,;:") == []


def test_mixed_case_and_casefold() -> None:
    assert toks("Hello WORLD hElLo") == ["hello", "world", "hello"]
    assert toks("Straße STRASSE") == ["strasse", "strasse"]  # casefold, not lower


def test_punctuation_splits() -> None:
    assert toks("Hello, world! (Yes.) _italic_") == ["hello", "world", "yes", "italic"]


def test_cyrillic() -> None:
    assert toks("Київ — столиця України.") == ["київ", "столиця", "україни"]


def test_combining_mark_accent() -> None:
    composed = "café"  # é as one code point
    decomposed = "cafe\u0301"  # e + COMBINING ACUTE ACCENT
    assert toks(decomposed) == toks(composed) == ["café"]


def test_apostrophes_inside_words() -> None:
    assert toks("Don't stop at o'clock") == ["don't", "stop", "at", "o'clock"]
    assert toks("П\u2019ять м'ячів") == ["п'ять", "м'ячів"]  # curly and straight unified


def test_quote_apostrophes_dropped() -> None:
    assert toks("'Tis the students' 'books'") == ["tis", "the", "students", "books"]


def test_hyphens_split() -> None:
    assert toks("well-known state-of-the-art--really") == [
        "well",
        "known",
        "state",
        "of",
        "the",
        "art",
        "really",
    ]


def test_digits() -> None:
    assert toks("In 1984 mp3 19th 3.14 1,000") == [
        "in",
        "1984",
        "mp3",
        "19th",
        "3",
        "14",
        "1",
        "000",
    ]


def test_modifier_letter_apostrophe_is_not_a_token() -> None:
    # Found by Hypothesis: U+02BC is a "letter" (Lm) for \w, so it used to become a
    # token "'" on its own, and a leading one survived ("\u02bctis" -> "'tis").
    assert toks("\u02bc") == []
    assert toks("\u02bctis") == ["tis"]
    assert toks("п\u02bcять") == ["п'ять"]
