import logging
import math

import pytest

from findex.index import DocMeta, Index, Posting
from findex.rank import BM25, SCORERS, Scorer, Searcher, SearchResult, TfIdf
from findex.timing import timed


def make_index(docs: dict[int, dict[str, int]], lengths: dict[int, int] | None = None) -> Index:
    """Index from {doc_id: {term: tf}}; length defaults to the sum of tfs."""
    postings: dict[str, list[Posting]] = {}
    for doc_id in sorted(docs):
        for term, tf in docs[doc_id].items():
            postings.setdefault(term, []).append(Posting(doc_id, tf))
    lengths = lengths or {d: sum(t.values()) for d, t in docs.items()}
    meta = {d: DocMeta(d, f"doc{d}.txt", f"doc {d}") for d in docs}
    return Index(postings, lengths, meta)


def test_scorers_follow_the_protocol():
    assert all(isinstance(s, Scorer) for s in SCORERS.values())
    assert isinstance(lambda term, posting, index: 1.0, Scorer)  # duck typing: any callable


def test_tfidf_formula():
    ix = make_index({0: {"x": 3}, 1: {"y": 1}, 2: {"y": 1}, 3: {"y": 1}})
    assert TfIdf()("x", ix["x"][0], ix) == pytest.approx((1 + math.log(3)) * math.log(4))


def test_bm25_formula():
    ix = make_index({0: {"x": 2, "y": 2}, 1: {"y": 4}})  # lengths 4 and 4, avg 4
    n, df, tf, k1, b = 2, 1, 2, 1.5, 0.75
    idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
    expected = idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * 1.0))
    assert BM25()("x", ix["x"][0], ix) == pytest.approx(expected)


def test_rare_term_beats_common_term():
    docs = {i: {"common": 1, "filler": 9} for i in range(10)}
    docs[3] = {"common": 1, "rare": 1, "filler": 8}
    docs[7] = {"rare": 1, "filler": 9}
    ix = make_index(docs)
    for scorer in SCORERS.values():
        top = Searcher(ix).search("common OR rare", scorer, k=3)
        assert {r.doc_id for r in top[:2]} == {3, 7}


def test_bm25_saturates_tfidf_does_not():
    ix = make_index({0: {"x": 1}, 1: {"y": 1}}, lengths={0: 100, 1: 100})
    bm25, tfidf = BM25(), TfIdf()

    def gain(scorer, tf):
        return scorer("x", Posting(0, tf), ix) - scorer("x", Posting(0, tf - 1), ix)

    first_bm25 = bm25("x", Posting(0, 1), ix)
    assert gain(bm25, 20) < 0.01 * first_bm25  # 20th repetition: < 1% of the first
    assert bm25("x", Posting(0, 10**6), ix) < bm25.k1 + 1  # bounded by idf·(k1+1), idf<1 here
    assert gain(tfidf, 20) > 0.02 * tfidf("x", Posting(0, 1), ix)  # log keeps growing


def test_short_doc_beats_long_doc_with_bm25_only():
    ix = make_index({0: {"x": 1, "pad": 9}, 1: {"x": 1, "pad": 999}, 2: {"pad": 50}})
    bm25_top = Searcher(ix).search("x", BM25())
    assert [r.doc_id for r in bm25_top] == [0, 1]
    assert bm25_top[0].score > bm25_top[1].score
    tfidf_top = Searcher(ix).search("x", TfIdf())
    assert tfidf_top[0].score == tfidf_top[1].score  # TF-IDF ignores length
    assert BM25(b=0)("x", ix["x"][0], ix) == BM25(b=0)("x", ix["x"][1], ix)  # b=0: no norm


def test_search_top_k_sorted_and_ordering():
    ix = make_index({i: {"x": i + 1, "pad": 10} for i in range(20)})
    results = Searcher(ix).search("x", k=5)
    assert len(results) == 5
    assert results == sorted(results, reverse=True)
    assert results[0] > results[-1]  # order=True compares (score, doc_id)
    assert SearchResult(1.0, 1, "a") == SearchResult(1.0, 1, "different title")


def test_negated_only_query_returns_matches_with_zero_score(index):
    results = Searcher(index).search("NOT ship")
    assert [(r.doc_id, r.score) for r in results] == [(3, 0.0)]


def test_match_cache_hit_is_logged(index, caplog):
    searcher = Searcher(index)
    with caplog.at_level(logging.DEBUG):
        searcher.search("whale ship")
        searcher.search("Whale AND ship")  # same tree -> cache hit
    assert searcher.cache_info.hits == 1 and searcher.cache_info.misses == 1
    assert "Searcher.search took" in caplog.text
    assert "hits=1" in caplog.text


def test_timed_keeps_metadata_and_logs_on_error(caplog):
    @timed
    def boom():
        """Docstring survives."""
        raise RuntimeError

    assert boom.__name__ == "boom" and boom.__doc__ == "Docstring survives."
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        boom()
    assert "boom took" in caplog.text
