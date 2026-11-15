"""Ranked search: Boolean match set, then TF-IDF or BM25 scores, then top-k.

    searcher = Searcher(index)
    for result in searcher.search('whale "sperm whale" NOT ship', scorer=BM25(), k=5):
        print(result)

Scorers follow the `Scorer` protocol — any callable ``(term, posting, index) ->
float`` fits, no base class needed (duck typing). Scores are accumulated per
document in a dict and the best k are taken with ``heapq.nlargest`` —
O(n log k) instead of sorting all n candidates.
"""

from __future__ import annotations

import heapq
import logging
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, Protocol, runtime_checkable

from findex.corpus import read_text
from findex.engines import EngineName
from findex.index import Index, Posting
from findex.query import Node, match, parse
from findex.snippet import make_snippet
from findex.timing import timed

log = logging.getLogger(__name__)


# --- scorers ------------------------------------------------------------------------


@runtime_checkable
class Scorer(Protocol):
    """Anything that can score one (term, posting) pair."""

    def __call__(self, term: str, posting: Posting, index: Index) -> float: ...


@dataclass(frozen=True)
class TfIdf:
    """(1 + ln tf) · ln(N / df).

    Log-scaled tf (the 10th occurrence is not worth 10× the first) and classic idf:
    a term in every document gets idf = 0. No document-length normalisation.
    """

    def __call__(self, term: str, posting: Posting, index: Index) -> float:
        idf = math.log(index.num_docs / index.df(term))
        return (1 + math.log(posting.tf)) * idf


@dataclass(frozen=True)
class BM25:
    """Okapi BM25 (the Lucene/Elasticsearch variant)::

        idf   = ln(1 + (N - df + 0.5) / (df + 0.5))           # never negative
        score = idf · tf·(k1 + 1) / (tf + k1·(1 - b + b·dl/avgdl))

    * k1 — tf saturation: the score approaches idf·(k1 + 1) as tf grows, so the
      20th repetition adds almost nothing. k1 = 0 ignores tf completely.
    * b  — length normalisation: a long document needs more occurrences for the
      same score. b = 0 turns normalisation off, b = 1 normalises fully.
    """

    k1: float = 1.5
    b: float = 0.75

    def __call__(self, term: str, posting: Posting, index: Index) -> float:
        n, df = index.num_docs, index.df(term)
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        length_ratio = index.doc_length(posting.doc_id) / index.avg_doc_length
        tf = posting.tf
        return idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length_ratio))


type ScorerName = Literal["bm25", "tfidf"]
SCORERS: Final[dict[ScorerName, Scorer]] = {"bm25": BM25(), "tfidf": TfIdf()}
DEFAULT_SCORER: Final[Scorer] = SCORERS["bm25"]


def get_scorer(name: ScorerName) -> Scorer:
    return SCORERS[name]


# --- results ------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class SearchResult:
    """One hit. Ordered by (score, doc_id): ``sorted(results, reverse=True)`` = best first.

    `title` and `snippet` are excluded from comparisons (``compare=False``): two
    hits are equal/ordered only by what the ranking produced.
    """

    score: float
    doc_id: int
    title: str = field(compare=False)
    snippet: str = field(default="", compare=False)

    def __str__(self) -> str:
        title = self.title if len(self.title) <= 70 else self.title[:69] + "…"
        line = f"{self.score:7.3f}  [{self.doc_id:>4}] {title}"
        return f"{line}\n           {self.snippet}" if self.snippet else line


# --- searcher -----------------------------------------------------------------------


def unique(items: Iterator[str]) -> list[str]:
    return list(dict.fromkeys(items))


class Searcher:
    """Parses, matches (cached), scores and returns the top k for one index.

    The Boolean match is cached with ``functools.lru_cache(maxsize=256)``, keyed by
    the parsed *tree*: nodes are frozen dataclasses, hence hashable, so ``Whale
    ship`` and ``whale AND ship`` share one cache entry. The cache lives on the
    instance — ``@lru_cache`` on the method at class level would key on ``self``
    and keep every Searcher (and its index) alive for the life of the process.
    """

    def __init__(
        self,
        index: Index,
        engine: EngineName = "set",
        texts: Callable[[int], str | None] | None = None,
    ) -> None:
        self.index = index
        self.engine: EngineName = engine
        self._texts = texts  # doc_id -> full text, for snippets (default: read .txt files)
        self.last_match_count = 0
        self._match_cached = lru_cache(maxsize=256)(self._match)
        self.match = timed(self._match_cached)

    def _match(self, tree: Node) -> frozenset[int]:
        return frozenset(match(tree, self.index, self.engine))

    @property
    def cache_hits(self) -> int:
        return self._match_cached.cache_info().hits

    @property
    def cache_misses(self) -> int:
        return self._match_cached.cache_info().misses

    @timed
    def search(
        self,
        query: str | Node,
        scorer: Scorer = DEFAULT_SCORER,
        k: int = 10,
        snippets: bool = False,
    ) -> list[SearchResult]:
        tree = parse(query) if isinstance(query, str) else query
        matched = self.match(tree)
        self.last_match_count = len(matched)
        log.debug("match cache: %s", self._match_cached.cache_info())

        terms = unique(tree.terms())
        scores = dict.fromkeys(matched, 0.0)  # docs matched only through NOT keep score 0
        for term in terms:
            for posting in self.index.get(term, ()):
                if posting.doc_id in scores:
                    scores[posting.doc_id] += scorer(term, posting, self.index)

        # Best k by score; ties broken by the smaller doc_id, so output is deterministic.
        top = heapq.nlargest(k, scores.items(), key=lambda item: (item[1], -item[0]))
        results: list[SearchResult] = []
        for doc_id, score in top:
            meta = self.index.doc_meta[doc_id]
            snippet = self.snippet(doc_id, terms) if snippets else ""
            results.append(SearchResult(score, doc_id, meta.title, snippet))
        return results

    def snippet(self, doc_id: int, terms: list[str]) -> str:
        if self._texts is not None:
            text = self._texts(doc_id)
            return make_snippet(text, terms) if text else ""
        path = Path(self.index.doc_meta[doc_id].path)
        if path.suffix != ".txt" or not path.exists():
            return ""  # JSON-lines documents have no file of their own
        return make_snippet(read_text(path), terms)
