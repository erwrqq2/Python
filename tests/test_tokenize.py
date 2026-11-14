"""Tokenizer checks (plain asserts; runnable with pytest)."""

import inspect

from findex.tokenize import tokenize


def toks(text: str) -> list[str]:
    return list(tokenize(text))


def test_is_generator():
    assert inspect.isgeneratorfunction(tokenize)
    assert inspect.isgenerator(tokenize("hello"))


def test_empty_and_punctuation_only():
    assert toks("") == []
    assert toks("... !!! -- ,;:") == []


def test_mixed_case_and_casefold():
    assert toks("Hello WORLD hElLo") == ["hello", "world", "hello"]
    assert toks("Straße STRASSE") == ["strasse", "strasse"]  # casefold, not lower


def test_punctuation_splits():
    assert toks("Hello, world! (Yes.) _italic_") == ["hello", "world", "yes", "italic"]


def test_cyrillic():
    assert toks("Київ — столиця України.") == ["київ", "столиця", "україни"]


def test_combining_mark_accent():
    composed = "café"  # é as one code point
    decomposed = "café"  # e + COMBINING ACUTE ACCENT
    assert toks(decomposed) == toks(composed) == ["café"]


def test_apostrophes_inside_words():
    assert toks("Don't stop at o'clock") == ["don't", "stop", "at", "o'clock"]
    assert toks("П’ять м'ячів") == ["п'ять", "м'ячів"]  # curly and straight unified


def test_quote_apostrophes_dropped():
    assert toks("'Tis the students' 'books'") == ["tis", "the", "students", "books"]


def test_hyphens_split():
    assert toks("well-known state-of-the-art--really") == [
        "well",
        "known",
        "state",
        "of",
        "the",
        "art",
        "really",
    ]


def test_digits():
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
