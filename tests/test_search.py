import random

import pytest

from findex.search import ENGINES, QueryError, difference, evaluate, intersect, parse, union


def test_merge_ops_match_set_ops():
    rng = random.Random(1)
    for _ in range(200):
        a = sorted(rng.sample(range(50), rng.randint(0, 30)))
        b = sorted(rng.sample(range(50), rng.randint(0, 30)))
        assert intersect(a, b) == sorted(set(a) & set(b))
        assert union(a, b) == sorted(set(a) | set(b))
        assert difference(a, b) == sorted(set(a) - set(b))


@pytest.mark.parametrize("engine", ENGINES.values(), ids=ENGINES.keys())
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("whale", [0, 2]),
        ("whale ship", [0, 2]),  # implicit AND
        ("whale AND shark", [2]),
        ("shark OR whales", [0, 1, 2]),
        ("ship NOT shark", [0, 1]),
        ("NOT ship", [3]),
        ("unrelated OR NOT ship", [3]),
        ("Whale SHIP", [0, 2]),  # terms are tokenized/casefolded like documents
        ("whale OR text NOT ship", [3]),  # strictly left to right: (whale OR text) NOT ship
        ("dragon", []),
        ("dragon OR shark", [2]),
    ],
)
def test_queries(index, engine, query, expected):
    assert evaluate(query, index, engine) == expected


def test_hyphenated_word_is_and_of_its_tokens(index):
    assert evaluate("whale-ship", index) == evaluate("whale ship", index)


@pytest.mark.parametrize("query", ["", "AND whale", "whale OR", "whale NOT", "...", "a OR OR b"])
def test_bad_queries(query):
    with pytest.raises(QueryError):
        parse(query)
