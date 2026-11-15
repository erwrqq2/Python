"""Lab 3 scorer vs NumPy scorer on the same index and queries (Lab 8).

    python -m findex.bench_numpy index.fidx "the" "locomotive" "river ship captain"

Both searchers have the Boolean match cached (it is the same code for both), so
the time is scoring + top-k: Lab 3 loops over every posting in Python and calls
the scorer per posting; NumPy scores a whole posting list per term with a few
array operations. Best of 7 repeats of `timeit` (number chosen automatically).
"""

from __future__ import annotations

import argparse
import timeit
from functools import partial
from pathlib import Path

from findex.arrays import ArrayIndex
from findex.query import parse
from findex.rank import SCORERS, Searcher
from findex.store import load
from findex.vector import VectorSearcher


def best_ms(func: partial[object], repeat: int = 7) -> float:
    timer = timeit.Timer(func)
    number, _ = timer.autorange()
    return min(timer.repeat(repeat=repeat, number=number)) / number * 1000


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.bench_numpy", description=__doc__)
    parser.add_argument("index", type=Path)
    parser.add_argument("queries", nargs="+")
    parser.add_argument("-k", type=int, default=10)
    args = parser.parse_args(argv)

    index = load(args.index)
    arrays = ArrayIndex.from_index(index)
    lab3, vectorised = Searcher(index), VectorSearcher(arrays)
    print(
        "| Query | Postings scored | Docs matched | Lab 3 scorer, ms | NumPy scorer, ms | Speedup |"
    )
    print("|---|---|---|---|---|---|")
    for query in args.queries:
        tree = parse(query)
        a = lab3.search(tree, SCORERS["bm25"], k=args.k)
        b = vectorised.search(tree, "bm25", k=args.k)
        assert [(r.doc_id, r.score) for r in a] == [(r.doc_id, r.score) for r in b]
        postings = sum(arrays.df(t) for t in dict.fromkeys(tree.terms()))
        slow = best_ms(partial(lab3.search, tree, SCORERS["bm25"], args.k))
        fast = best_ms(partial(vectorised.search, tree, "bm25", args.k))
        matched = len(vectorised.match(tree))
        print(
            f"| `{query}` | {postings:,} | {matched:,} | {slow:.3f} | {fast:.3f} | "
            f"**{slow / fast:.1f}×** |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
