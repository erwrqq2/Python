"""Vectorised ranking over `ArrayIndex` (Lab 8): BM25 / TF-IDF as array expressions.

For every query term the work is a handful of NumPy operations over its whole
posting list (``scores[doc_ids] += ...``) instead of one Python call per
posting. The formulas are written with the same operations in the same order
as `findex.rank.BM25` / `TfIdf`, so the scores are bit-for-bit the same as in
Lab 3, and the ranking (score desc, then doc_id asc) is identical — tested.

Top-k uses ``np.argpartition`` (O(n)) instead of sorting all candidates
(O(n log n)); ties at the k-th place are resolved exactly like Lab 3's
``heapq.nlargest`` with key ``(score, -doc_id)``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import numpy as np
from numpy.typing import NDArray

from findex.arrays import ArrayIndex
from findex.corpus import read_text
from findex.engines import EngineName
from findex.query import Node, match, parse
from findex.rank import ScorerName, SearchResult
from findex.snippet import make_snippet
from findex.timing import timed

if TYPE_CHECKING:
    from findex.semantic import SemanticIndex

log = logging.getLogger(__name__)

type FloatArray = NDArray[np.float64]
type Mode = Literal["keyword", "semantic", "hybrid"]
MODES: Final[tuple[Mode, ...]] = ("keyword", "semantic", "hybrid")
RRF_K: Final = 60  # Reciprocal Rank Fusion constant (Cormack et al., 2009)


def bm25_scores(
    index: ArrayIndex, terms: list[str], k1: float = 1.5, b: float = 0.75
) -> FloatArray:
    """BM25 for every document at once (0 where no query term occurs)."""
    n, avg = index.num_docs, index.avg_doc_length
    scores = np.zeros(n, dtype=np.float64)
    for term in terms:
        ids, tfs = index.arrays(term)
        if not len(ids):
            continue
        df = len(ids)
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))  # one scalar per term
        tf = tfs.astype(np.float64)
        length_ratio = index.doc_len[ids] / avg
        scores[ids] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length_ratio))
    return scores


def tfidf_scores(index: ArrayIndex, terms: list[str]) -> FloatArray:
    """(1 + ln tf) · ln(N / df) for every document at once."""
    n = index.num_docs
    scores = np.zeros(n, dtype=np.float64)
    for term in terms:
        ids, tfs = index.arrays(term)
        if not len(ids):
            continue
        idf = math.log(n / len(ids))
        scores[ids] += (1 + np.log(tfs.astype(np.float64))) * idf
    return scores


ARRAY_SCORERS: Final[dict[ScorerName, Callable[[ArrayIndex, list[str]], FloatArray]]] = {
    "bm25": bm25_scores,
    "tfidf": tfidf_scores,
}


def top_k(candidates: NDArray[np.int64], scores: FloatArray, k: int) -> list[tuple[int, float]]:
    """Best `k` of `candidates` by score desc, then doc_id asc; argpartition, not a sort."""
    cand_scores = scores[candidates]
    k = min(k, len(candidates))
    if k <= 0:
        return []
    if k < len(candidates):
        part = np.argpartition(-cand_scores, k - 1)
        kth = cand_scores[part[k - 1]]
        keep = cand_scores >= kth  # every candidate tied with the k-th place stays in
        candidates, cand_scores = candidates[keep], cand_scores[keep]
    order = np.lexsort((candidates, -cand_scores))[:k]  # last key first: score desc, id asc
    return [(int(candidates[i]), float(cand_scores[i])) for i in order]


def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion: score(d) = Σ 1 / (k + rank of d in each list), rank from 1."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


class VectorSearcher:
    """Keyword (vectorised BM25/TF-IDF), semantic and hybrid search over an `ArrayIndex`."""

    def __init__(
        self,
        index: ArrayIndex,
        engine: EngineName = "set",
        texts: Callable[[int], str | None] | None = None,
        semantic: SemanticIndex | None = None,
    ) -> None:
        self.index = index
        self.engine: EngineName = engine
        self.semantic = semantic
        self._texts = texts
        self._match_cached = lru_cache(maxsize=256)(self._match)

    def _match(self, tree: Node) -> NDArray[np.int64]:
        ids = np.asarray(match(tree, self.index, self.engine), dtype=np.int64)
        ids.flags.writeable = False  # cached and shared between requests
        return ids

    def match(self, tree: Node) -> NDArray[np.int64]:
        """Sorted doc_ids matching the Boolean query (cached per tree)."""
        return self._match_cached(tree)

    def keyword(self, tree: Node, scorer: ScorerName, k: int) -> list[tuple[int, float]]:
        terms = list(dict.fromkeys(tree.terms()))
        scores = ARRAY_SCORERS[scorer](self.index, terms)
        return top_k(self.match(tree), scores, k)

    @timed
    def search(
        self,
        query: str | Node,
        scorer: ScorerName = "bm25",
        k: int = 10,
        snippets: bool = False,
        mode: Mode = "keyword",
        pool: int = 100,
    ) -> list[SearchResult]:
        """Top `k`. `pool` = how deep each list goes before hybrid fusion."""
        text = query if isinstance(query, str) else str(query)
        if mode == "keyword":
            tree = parse(query) if isinstance(query, str) else query
            ranked = self.keyword(tree, scorer, k)
        else:
            if self.semantic is None:
                raise ValueError("semantic search needs embeddings (run `findex embed`)")
            semantic = self.semantic.search(text, max(k, pool) if mode == "hybrid" else k)
            if mode == "semantic":
                ranked = semantic
            else:
                try:
                    keyword = self.keyword(parse(text), scorer, pool)
                except ValueError:  # not a valid keyword query: fuse semantic alone
                    keyword = []
                fused = rrf([[d for d, _ in keyword], [d for d, _ in semantic]])
                ranked = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:k]
        terms = _query_words(text)
        return [
            SearchResult(
                score,
                doc_id,
                self.index.doc_meta[doc_id].title,
                self.snippet(doc_id, terms, text) if snippets else "",
            )
            for doc_id, score in ranked
        ]

    def snippet(self, doc_id: int, terms: list[str], query: str = "") -> str:
        text = self._texts(doc_id) if self._texts is not None else _read_doc(self, doc_id)
        if not text:
            return ""
        found = make_snippet(text, terms)
        if found or self.semantic is None or not query:
            return found
        passage = self.semantic.best_passage(doc_id, text, query)  # hit without shared words
        return passage[:200] + ("…" if len(passage) > 200 else "")


def _query_words(text: str) -> list[str]:
    try:
        return list(dict.fromkeys(parse(text).terms()))
    except ValueError:
        from findex.tokenize import tokenize

        return list(dict.fromkeys(tokenize(text)))


def _read_doc(searcher: VectorSearcher, doc_id: int) -> str | None:
    path = Path(searcher.index.doc_meta[doc_id].path)
    return read_text(path) if path.suffix == ".txt" and path.exists() else None
