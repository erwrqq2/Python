import random

import pytest

from findex.engines import ENGINE_NAMES, difference, intersect, union
from findex.index import Index
from findex.query import match, parse


def test_merge_ops_match_set_ops() -> None:
    rng = random.Random(1)
    for _ in range(200):
        a = sorted(rng.sample(range(50), rng.randint(0, 30)))
        b = sorted(rng.sample(range(50), rng.randint(0, 30)))
        assert intersect(a, b) == sorted(set(a) & set(b))
        assert union(a, b) == sorted(set(a) | set(b))
        assert difference(a, b) == sorted(set(a) - set(b))


@pytest.mark.parametrize(
    "query",
    ["whale ship", "shark OR whales", "ship NOT shark", "NOT ship", "(whale OR text) NOT ship"],
)
def test_engines_agree(index: Index, query: str) -> None:
    results = {name: match(parse(query), index, name) for name in ENGINE_NAMES}
    assert results["merge"] == results["set"]
