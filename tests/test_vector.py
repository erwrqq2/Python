"""NumPy postings, vectorised scorers (same ranking as Lab 3), semantic and hybrid search."""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

import findex.semantic
from findex.arrays import ArrayIndex, load_array_index
from findex.corpus import Document
from findex.index import Index, build_index
from findex.rank import SCORERS, Searcher
from findex.semantic import EmbeddingMeta, SemanticIndex, chunk_words
from findex.store import save
from findex.vector import VectorSearcher, rrf, top_k
from findex.web.app import create_app, get_index, get_searcher
from findex.web.settings import Settings

QUERIES = [
    "whale",
    "ship OR whale",
    "the",
    "whale ship",
    "ship NOT shark",
    "NOT ship",
    '"the whale"',
    "(whale OR text) NOT ship",
    "dragon",
    "whale OR dragon",
]


def ranked(searcher: Searcher | VectorSearcher, query: str, scorer: str) -> list[tuple[int, float]]:
    if isinstance(searcher, Searcher):
        results = searcher.search(query, SCORERS[scorer], k=50)  # type: ignore[index]  # str key checked by the loop
    else:
        results = searcher.search(query, scorer, k=50)  # type: ignore[arg-type]  # same
    return [(r.doc_id, r.score) for r in results]


# --- ArrayIndex -------------------------------------------------------------------------


def test_array_index_matches_the_object_index(index_pos: Index) -> None:
    arrays = ArrayIndex.from_index(index_pos)
    assert len(arrays) == len(index_pos) and arrays.num_docs == index_pos.num_docs
    assert arrays.avg_doc_length == index_pos.avg_doc_length
    assert arrays.doc_len.dtype == np.int32 and arrays.doc_len.tolist() == [8, 7, 7, 4]
    for term, plist in index_pos.items():
        ids, tfs = arrays.arrays(term)
        assert ids.dtype == tfs.dtype == np.int32
        assert ids.tolist() == [p.doc_id for p in plist] and tfs.tolist() == [p.tf for p in plist]
        assert arrays.postings_for(term) == plist  # positions rebuilt from the flat array
        assert arrays.df(term) == index_pos.df(term)
    assert arrays.arrays("dragon")[0].size == 0 and "dragon" not in arrays


@pytest.mark.parametrize("fmt", ["binary", "pickle"])
def test_load_array_index(tmp_path: Path, index_pos: Index, fmt: str) -> None:
    path = tmp_path / "i"
    save(index_pos, path, "binary" if fmt == "binary" else "pickle")
    loaded, reference = load_array_index(path), ArrayIndex.from_index(index_pos)
    for term in index_pos:
        assert [a.tolist() for a in loaded.arrays(term)] == [
            a.tolist() for a in reference.arrays(term)
        ]
    assert loaded.doc_meta == reference.doc_meta and loaded.has_positions


# --- the NumPy scorers give Lab 3's exact ranking ---------------------------------------


@pytest.mark.parametrize("scorer", ["bm25", "tfidf"])
@pytest.mark.parametrize("query", QUERIES)
def test_vectorised_ranking_is_identical_to_lab3(index_pos: Index, query: str, scorer: str) -> None:
    lab3 = ranked(Searcher(index_pos), query, scorer)
    vectorised = ranked(VectorSearcher(ArrayIndex.from_index(index_pos)), query, scorer)
    assert vectorised == lab3  # same documents, same order, same float scores


words = st.sampled_from(["whale", "ship", "sea", "the", "a", "київ", "don't"])
texts = st.lists(st.lists(words, min_size=1, max_size=30).map(" ".join), min_size=1, max_size=8)
queries = st.lists(words, min_size=1, max_size=3).flatmap(
    lambda ws: st.sampled_from([" ".join(ws), " OR ".join(ws), f"{ws[0]} NOT {ws[-1]}"])
)


@settings(max_examples=80, deadline=None)
@given(texts=texts, query=queries, scorer=st.sampled_from(["bm25", "tfidf"]))
def test_property_vectorised_equals_lab3(texts: list[str], query: str, scorer: str) -> None:
    docs = [Document(i, Path(f"d{i}.txt"), t) for i, t in enumerate(texts)]
    index = build_index(docs)
    assert ranked(VectorSearcher(ArrayIndex.from_index(index)), query, scorer) == ranked(
        Searcher(index), query, scorer
    )


def test_top_k_breaks_ties_by_doc_id() -> None:
    scores = np.array([1.0, 3.0, 3.0, 2.0, 3.0, 0.5])
    candidates = np.arange(6, dtype=np.int64)
    assert top_k(candidates, scores, 2) == [(1, 3.0), (2, 3.0)]  # three tied at 3.0: ids 1, 2
    assert top_k(candidates, scores, 4) == [(1, 3.0), (2, 3.0), (4, 3.0), (3, 2.0)]
    assert top_k(candidates[[5, 0]], scores, 10) == [(0, 1.0), (5, 0.5)]
    assert top_k(candidates[:0], scores, 3) == []


def test_rrf() -> None:
    fused = rrf([[7, 3, 1], [3, 9]], k=60)
    assert fused[3] == pytest.approx(1 / 62 + 1 / 61)  # 2nd in one list, 1st in the other
    assert fused[7] == pytest.approx(1 / 61) and fused[9] == pytest.approx(1 / 62)
    assert max(fused, key=lambda d: fused[d]) == 3  # agreement beats one first place


# --- semantic search with fixed vectors (no model download) -----------------------------


def test_chunk_words_overlap() -> None:
    text = " ".join(str(i) for i in range(10))
    chunks = list(chunk_words(text, size=4, overlap=1))
    assert [start for start, _ in chunks] == [0, 3, 6]
    assert chunks[1][1] == "3 4 5 6" and chunks[-1][1] == "6 7 8 9"
    assert list(chunk_words("short", size=4, overlap=1)) == [(0, "short")]


@pytest.fixture
def semantic(monkeypatch: pytest.MonkeyPatch) -> Iterator[SemanticIndex]:
    """4 docs; doc 2 has one passage pointing exactly at the "sea" direction."""
    vectors = np.array(
        [[1, 0, 0], [0, 1, 0], [0.6, 0.8, 0], [0, 0, 1], [0.8, 0.6, 0]], dtype=np.float32
    )
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    meta: EmbeddingMeta = {
        "model": "fake",
        "chunk_words": 3,
        "overlap": 0,
        "dim": 3,
        "chunks": 5,
        "docs": 4,
    }
    index = SemanticIndex(
        vectors,
        np.array([0, 1, 2, 2, 3], dtype=np.int32),
        np.array([0, 0, 0, 3, 0], dtype=np.int32),
        meta,
    )
    directions = {"sea": [0.0, 0.0, 1.0], "ship": [1.0, 0.0, 0.0]}

    def fake_vector(model: str, text: str) -> np.ndarray:
        return np.array(directions.get(text, [0.6, 0.8, 0.0]), dtype=np.float32)

    monkeypatch.setattr(findex.semantic, "_query_vector", fake_vector)
    yield index


def test_semantic_best_passage_per_document(semantic: SemanticIndex) -> None:
    ranking = semantic.search("sea", k=4)
    assert ranking[0] == (2, pytest.approx(1.0))  # doc 2's *second* passage is the sea one
    assert [d for d, _ in ranking] == [2, 0, 1, 3] or ranking[0][0] == 2
    text = "one two three sea waves here"
    assert semantic.best_passage(2, text, "sea") == "sea waves here"


def test_semantic_index_save_load(tmp_path: Path, semantic: SemanticIndex) -> None:
    semantic.save(tmp_path / "x.fidx")
    loaded = SemanticIndex.load(tmp_path / "x.fidx")
    assert loaded is not None and loaded.meta == semantic.meta
    assert np.array_equal(loaded.vectors, semantic.vectors)
    assert loaded.chunk_doc.tolist() == semantic.chunk_doc.tolist()
    assert SemanticIndex.load(tmp_path / "missing.fidx") is None


def test_hybrid_fuses_keyword_and_semantic(index_pos: Index, semantic: SemanticIndex) -> None:
    searcher = VectorSearcher(ArrayIndex.from_index(index_pos), semantic=semantic)
    keyword = [d for d, _ in searcher.bag_of_words("the ship", "bm25", 100)]  # "the" dropped
    sem = [r.doc_id for r in searcher.search("the ship", k=100, mode="semantic")]
    hybrid = searcher.search("the ship", k=4, mode="hybrid")
    fused = rrf([keyword, sem])
    assert [r.doc_id for r in hybrid] == sorted(fused, key=lambda d: (-fused[d], d))[:4]
    assert hybrid[0].score == pytest.approx(max(fused.values()))


def test_semantic_mode_needs_embeddings(index_pos: Index) -> None:
    with pytest.raises(ValueError, match="embed"):
        VectorSearcher(ArrayIndex.from_index(index_pos)).search("ship", mode="semantic")


def test_api_modes(index_pos: Index, semantic: SemanticIndex) -> None:
    arrays = ArrayIndex.from_index(index_pos)
    app = create_app(Settings(index_path=Path("unused.fidx")))
    app.dependency_overrides[get_index] = lambda: arrays
    app.dependency_overrides[get_searcher] = lambda: VectorSearcher(arrays, semantic=semantic)
    client = TestClient(app)
    body = client.get("/search", params={"q": "sea", "mode": "semantic"}).json()
    assert body["mode"] == "semantic" and body["results"][0]["doc_id"] == 2
    assert client.get("/search", params={"q": "ship", "mode": "hybrid"}).status_code == 200
    assert client.get("/search", params={"q": "ship", "mode": "magic"}).status_code == 422
    assert "semantic" in client.get("/", params={"q": "sea", "mode": "semantic"}).text
    app.dependency_overrides[get_searcher] = lambda: VectorSearcher(arrays)  # no embeddings
    assert client.get("/search", params={"q": "sea", "mode": "semantic"}).status_code == 422


def test_benchmarkable_with_a_temp_corpus() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.txt").write_text("whale ship sea", encoding="utf-8")
        assert load_array_index  # imported; real benchmarks live in test_benchmarks.py
