"""Precision@5 of TF-IDF vs BM25 on hand-labelled queries.

    uv run python -m findex.evaluate index.fidx evaluation/queries.json [--details]

queries.json: ``[{"query": "...", "relevant": ["<gutenberg id>", ...]}, ...]`` —
documents are named by file stem (data/gutenberg/<id>.txt), so the labels do
not depend on doc_ids.

P@5 = (relevant documents among the top 5) / 5. With fewer than 5 relevant
documents the best possible P@5 is len(relevant)/5, shown as "max".
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NotRequired, TypedDict, cast

from findex.arrays import ArrayIndex, load_array_index
from findex.index import Index
from findex.query import QueryError
from findex.rank import SCORERS, ScorerName, Searcher, SearchResult
from findex.semantic import SemanticIndex
from findex.store import open_index
from findex.vector import Mode, VectorSearcher

K: Final = 5
NAMES: Final[dict[ScorerName, str]] = {"bm25": "BM25", "tfidf": "TF-IDF"}
MODE_COLUMNS: Final[dict[str, Mode]] = {
    "keyword (BM25)": "keyword",
    "semantic": "semantic",
    "hybrid (RRF)": "hybrid",
}


class LabelledQuery(TypedDict):
    query: str
    relevant: list[str]  # file stems of documents that should be in the top 5
    why: NotRequired[str]


@dataclass(frozen=True)
class QueryScore:
    query: str
    relevant: frozenset[int]
    top: dict[str, list[SearchResult]]  # column name -> top 5

    def precision(self, scorer: str) -> float:
        return sum(r.doc_id in self.relevant for r in self.top[scorer]) / K

    @property
    def best_possible(self) -> float:
        return min(len(self.relevant), K) / K


def load_queries(path: Path) -> list[LabelledQuery]:
    # json.loads returns Any; the file format is documented above.
    return cast(list[LabelledQuery], json.loads(path.read_text(encoding="utf-8")))


def evaluate(index: Index, queries: list[LabelledQuery]) -> list[QueryScore]:
    by_stem = {Path(m.path).stem: d for d, m in index.doc_meta.items()}
    searcher = Searcher(index)
    scores: list[QueryScore] = []
    for q in queries:
        relevant = frozenset(by_stem[s] for s in q["relevant"])
        top: dict[str, list[SearchResult]] = {
            NAMES[name]: searcher.search(q["query"], scorer, k=K)
            for name, scorer in SCORERS.items()
        }
        scores.append(QueryScore(q["query"], relevant, top))
    return scores


def evaluate_modes(
    index: ArrayIndex, semantic: SemanticIndex, queries: list[LabelledQuery]
) -> list[QueryScore]:
    """P@5 of keyword (BM25), semantic and hybrid search (Lab 8)."""
    by_stem = {Path(m.path).stem: d for d, m in index.doc_meta.items()}
    searcher = VectorSearcher(index, semantic=semantic)
    scores: list[QueryScore] = []
    for q in queries:
        relevant = frozenset(by_stem[s] for s in q["relevant"])
        top: dict[str, list[SearchResult]] = {}
        for column, mode in MODE_COLUMNS.items():
            try:
                top[column] = searcher.search(q["query"], "bm25", k=K, mode=mode)
            except QueryError:
                top[column] = []
        scores.append(QueryScore(q["query"], relevant, top))
    return scores


def format_table(scores: list[QueryScore]) -> str:
    names = list(scores[0].top) if scores else []
    lines = [
        "| Query | Relevant | max | " + " | ".join(f"{n} P@5" for n in names) + " |",
        "|---|---|---|" + "---|" * len(names),
    ]
    for s in scores:
        cells = " | ".join(f"{s.precision(n):.1f}" for n in names)
        lines.append(f"| `{s.query}` | {len(s.relevant)} | {s.best_possible:.1f} | {cells} |")
    means = " | ".join(
        f"**{sum(s.precision(n) for s in scores) / len(scores):.2f}**" for n in names
    )
    lines.append(f"| **mean** | | | {means} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.evaluate", description=__doc__)
    parser.add_argument("index", type=Path)
    parser.add_argument("queries", type=Path, nargs="+", help="one or more query files")
    parser.add_argument("--details", action="store_true", help="print each top 5")
    parser.add_argument(
        "--modes", action="store_true", help="keyword / semantic / hybrid (needs `findex embed`)"
    )
    args = parser.parse_args(argv)
    queries = [q for path in args.queries for q in load_queries(path)]

    if args.modes:
        index = load_array_index(args.index)
        semantic = SemanticIndex.load(args.index)
        if semantic is None:
            parser.error(f"no embeddings for {args.index}: run `findex embed {args.index}`")
        scores = evaluate_modes(index, semantic, queries)
    else:
        with open_index(args.index) as obj_index:
            scores = evaluate(obj_index, queries)
    print(format_table(scores))
    if args.details:
        for s in scores:
            for name, top in s.top.items():
                print(f"\n{s.query} [{name}]")
                for r in top:
                    mark = "+" if r.doc_id in s.relevant else "-"
                    print(f"  {mark} {r.score:7.3f} [{r.doc_id}] {r.title[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
