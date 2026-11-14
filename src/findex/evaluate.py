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
from pathlib import Path

from findex.rank import SCORERS, Searcher
from findex.store import open_index

K = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.evaluate", description=__doc__)
    parser.add_argument("index", type=Path)
    parser.add_argument("queries", type=Path)
    parser.add_argument("--details", action="store_true", help="print each top 5")
    args = parser.parse_args(argv)
    queries = json.loads(args.queries.read_text(encoding="utf-8"))

    with open_index(args.index) as index:
        by_stem = {Path(m.path).stem: d for d, m in index.doc_meta.items()}
        searcher = Searcher(index)
        rows, totals, details = [], dict.fromkeys(SCORERS, 0.0), []
        for q in queries:
            relevant = {by_stem[s] for s in q["relevant"]}
            cells = []
            for name, scorer in SCORERS.items():
                top = searcher.search(q["query"], scorer, k=K)
                hits = sum(r.doc_id in relevant for r in top)
                totals[name] += hits / K
                cells.append(f"{hits / K:.1f}")
                details.append((q["query"], name, top, relevant))
            best = min(len(relevant), K) / K
            rows.append(
                f"| `{q['query']}` | {len(relevant)} | {best:.1f} | " + " | ".join(cells) + " |"
            )

    names = {"tfidf": "TF-IDF", "bm25": "BM25"}
    print("| Query | Relevant | max | " + " | ".join(names[n] + " P@5" for n in SCORERS) + " |")
    print("|---|---|---|" + "---|" * len(SCORERS))
    print("\n".join(rows))
    means = " | ".join(f"**{totals[n] / len(queries):.2f}**" for n in SCORERS)
    print(f"| **mean** | | | {means} |")

    if args.details:
        for query, name, top, relevant in details:
            print(f"\n{query} [{names[name]}]")
            for r in top:
                mark = "+" if r.doc_id in relevant else "-"
                print(f"  {mark} {r.score:7.3f} [{r.doc_id}] {r.title[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
