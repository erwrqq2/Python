"""Property-based tests (Hypothesis): statements that hold for *every* input,
checked on hundreds of generated ones. On failure Hypothesis shrinks the input
to a minimal counterexample."""

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from findex.corpus import Document
from findex.engines import difference, intersect, union
from findex.index import DocMeta, Index, Posting, build_index
from findex.query import And, Node, Not, Or, Phrase, Term, parse
from findex.rank import BM25
from findex.store import FORMATS, load, save
from findex.tokenize import tokenize

WORDS = ["whale", "ship", "sea", "the", "a", "café", "київ", "don't", "п'ять", "1984", "Ship"]

texts = st.lists(st.sampled_from(WORDS), max_size=40).map(" ".join)
corpora = st.lists(texts, min_size=1, max_size=8).map(
    lambda ts: [Document(i, Path(f"doc{i}.txt"), t) for i, t in enumerate(ts)]
)
sorted_ids = st.lists(st.integers(0, 60), max_size=40, unique=True).map(sorted)


# 1. load(save(index)) == index ----------------------------------------------------------


@settings(max_examples=60, deadline=None)  # deadline=None: file I/O timing varies on CI
@given(docs=corpora, positions=st.booleans(), fmt=st.sampled_from(FORMATS))
def test_save_load_round_trip(docs: list[Document], positions: bool, fmt: str) -> None:
    index = build_index(docs, positions=positions)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "index"
        save(index, path, "binary" if fmt == "binary" else "pickle")
        loaded = load(path)
    assert loaded.postings == index.postings
    assert loaded.doc_lengths == index.doc_lengths
    assert loaded.doc_meta == index.doc_meta
    assert loaded.has_positions == index.has_positions


# 2. postings are always sorted, unique and consistent -----------------------------------


@given(docs=corpora, positions=st.booleans())
def test_postings_invariants(docs: list[Document], positions: bool) -> None:
    index = build_index(docs, positions=positions)
    tf_per_doc = dict.fromkeys(index.doc_lengths, 0)
    for plist in index.values():
        ids = [p.doc_id for p in plist]
        assert ids == sorted(set(ids))  # strictly increasing
        for p in plist:
            assert p.tf > 0
            tf_per_doc[p.doc_id] += p.tf
            if positions:
                assert len(p.positions) == p.tf
                assert list(p.positions) == sorted(set(p.positions))
    assert tf_per_doc == index.doc_lengths  # every token is counted exactly once


# 3. two-pointer merge == set operations (oracle) ----------------------------------------


@given(a=sorted_ids, b=sorted_ids)
def test_merge_matches_set_operations(a: list[int], b: list[int]) -> None:
    assert intersect(a, b) == sorted(set(a) & set(b))
    assert union(a, b) == sorted(set(a) | set(b))
    assert difference(a, b) == sorted(set(a) - set(b))


# 4. the parser round-trips its own output ------------------------------------------------

simple_words = st.sampled_from(["a", "b", "whale", "ship", "café", "київ"])
leaves: st.SearchStrategy[Node] = st.one_of(
    simple_words.map(Term),
    st.lists(simple_words, min_size=2, max_size=3).map(lambda ws: Phrase(tuple(ws))),
)
trees: st.SearchStrategy[Node] = st.recursive(
    leaves,
    lambda children: st.one_of(
        st.builds(And, children, children),
        st.builds(Or, children, children),
        st.builds(Not, children),
    ),
    max_leaves=12,
)


@given(tree=trees)
def test_parse_of_str_is_identity(tree: Node) -> None:
    assert parse(str(tree)) == tree


# 5. tokenizer: no empty tokens, and tokenizing its own output changes nothing -----------


@given(text=st.text())
def test_tokenize_is_idempotent(text: str) -> None:
    tokens = list(tokenize(text))
    assert all(tokens)
    assert list(tokenize(" ".join(tokens))) == tokens


# 6. BM25: more occurrences never hurt, a longer document never helps --------------------


def _two_doc_index(length: int) -> Index:
    postings = {"x": [Posting(0, 1)], "y": [Posting(1, 1)]}
    meta = {0: DocMeta(0, "a", "a"), 1: DocMeta(1, "b", "b")}
    return Index(postings, {0: length, 1: 100}, meta)


@given(tf=st.integers(1, 1000), length=st.integers(1, 10_000), extra=st.integers(1, 1000))
def test_bm25_monotonic(tf: int, length: int, extra: int) -> None:
    bm25 = BM25()
    index = _two_doc_index(length)
    base = bm25("x", Posting(0, tf), index)
    assert bm25("x", Posting(0, tf + extra), index) >= base
    assert bm25("x", Posting(0, tf), _two_doc_index(length + extra)) <= base
