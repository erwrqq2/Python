"""Parser tests compare trees, not strings."""

import pytest

from findex.engines import ENGINE_NAMES, EngineName
from findex.index import Index
from findex.query import And, Node, Not, Or, Phrase, QueryError, Term, match, parse

a, b, c, d = Term("a"), Term("b"), Term("c"), Term("d")


@pytest.mark.parametrize(
    ("query", "tree"),
    [
        ("a", a),
        ("a b", And(a, b)),  # implicit AND
        ("a AND b", And(a, b)),
        ("a b c", And(And(a, b), c)),  # left-associative
        ("a OR b c", Or(a, And(b, c))),  # AND binds tighter than OR
        ("a b OR c", Or(And(a, b), c)),
        ("(a OR b) c", And(Or(a, b), c)),  # parentheses override
        ("a NOT b", And(a, Not(b))),
        ("NOT a OR b", Or(Not(a), b)),  # NOT binds tightest
        ("NOT NOT a", Not(Not(a))),
        ("a OR (b AND (c OR d))", Or(a, And(b, Or(c, d)))),
        ('"a b" c', And(Phrase(("a", "b")), c)),
        ('"a"', a),  # one-word phrase is just a term
        ("A and B", And(And(a, Term("and")), b)),  # lowercase "and" is a word
        ("Whale-Ship", Phrase(("whale", "ship"))),  # tokenizer splits hyphens -> phrase
        ("a , b", And(a, b)),  # pure punctuation is ignored
    ],
)
def test_parse_trees(query: str, tree: Node) -> None:
    assert parse(query) == tree


def test_lab_example() -> None:
    tree = parse('python AND (async OR await) NOT java "event loop"')
    expected = And(
        And(And(Term("python"), Or(Term("async"), Term("await"))), Not(Term("java"))),
        Phrase(("event", "loop")),
    )
    assert tree == expected


def test_operators_build_the_same_trees() -> None:
    assert (a | b & ~c) == Or(a, And(b, Not(c))) == parse("a OR b NOT c")
    assert hash(parse("Whale ship")) == hash(parse("whale AND ship"))  # cache key


@pytest.mark.parametrize(
    "query", ["", "   ", "AND a", "a OR", "a AND", "(a", "a)", "()", 'a "b', '""', "NOT", "..."]
)
def test_bad_queries(query: str) -> None:
    with pytest.raises(QueryError):
        parse(query)


@pytest.mark.parametrize("engine", ENGINE_NAMES)
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("whale", [0, 2]),
        ("whale ship", [0, 2]),
        ("whale AND shark", [2]),
        ("shark OR whales", [0, 1, 2]),
        ("ship NOT shark", [0, 1]),
        ("NOT ship", [3]),
        ("unrelated OR NOT ship", [3]),
        ("whale OR text NOT ship", [0, 2, 3]),  # precedence: whale OR (text NOT ship)
        ("(whale OR text) NOT ship", [3]),
        ("dragon", []),
    ],
)
def test_evaluate(index: Index, engine: EngineName, query: str, expected: list[int]) -> None:
    assert match(parse(query), index, engine) == expected


@pytest.mark.parametrize("engine", ENGINE_NAMES)
def test_phrases(index_pos: Index, engine: EngineName) -> None:
    assert match(parse('"the whale"'), index_pos, engine) == [0]
    assert match(parse('"whale near the ship"'), index_pos, engine) == [2]
    assert match(parse('"whale the"'), index_pos, engine) == []  # words present, wrong order
    assert match(parse('"whale ship" OR shark'), index_pos, engine) == [2]


def test_phrase_needs_positions(index: Index) -> None:
    with pytest.raises(QueryError, match="positions"):
        match(parse('"the whale"'), index)


def test_terms_skip_negated() -> None:
    assert list(parse('a "b c" NOT d (a OR NOT b)').terms()) == ["a", "b", "c", "a"]
