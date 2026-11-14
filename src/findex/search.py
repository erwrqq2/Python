"""Ranked search over a saved index.

    uv run python -m findex.search index.bin 'whale AND (harpoon OR lance) NOT "civil war"'
    uv run python -m findex.search index.bin "telegraph" "telegraph" -v      # 2nd = cache hit
    uv run python -m findex.search index.bin "insane asylum" --scorer tfidf -k 5

Query language: see `findex.query` (AND/OR/NOT, implicit AND, parentheses,
"phrases" — phrases need an index built with --positions).

`-v` prints the @timed log (load, search, match) and the match-cache counters
to stderr.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import tracemalloc
from pathlib import Path

from findex.engines import ENGINES
from findex.query import QueryError, parse
from findex.rank import SCORERS, Searcher
from findex.store import open_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.search", description="Ranked search.")
    parser.add_argument("index", type=Path, help="index file built by findex.index")
    parser.add_argument("queries", nargs="+", metavar="query", help="one or more queries")
    parser.add_argument("--scorer", choices=SCORERS, default="bm25")
    parser.add_argument("--engine", choices=ENGINES, default="set", help="Boolean matching")
    parser.add_argument("-k", type=int, default=10, help="how many results to show")
    parser.add_argument("--no-snippets", action="store_true")
    parser.add_argument("--memory", action="store_true", help="report peak memory (slower)")
    parser.add_argument("-v", "--verbose", action="store_true", help="timing / cache log")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="  %(levelname)s %(name)s: %(message)s",
    )
    sys.stdout.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
    for query in args.queries:  # fail fast on a bad query, before loading the index
        try:
            parse(query)
        except QueryError as exc:
            parser.error(f"{query!r}: {exc}")

    if args.memory:
        tracemalloc.start()
    t0 = time.perf_counter()
    with open_index(args.index) as index:
        t1 = time.perf_counter()
        print(f"{index!r} loaded in {t1 - t0:.2f} s, scorer={SCORERS[args.scorer]!r}")
        searcher = Searcher(index, ENGINES[args.engine])
        for query in args.queries:
            start = time.perf_counter()
            try:
                results = searcher.search(
                    query, SCORERS[args.scorer], k=args.k, snippets=not args.no_snippets
                )
            except QueryError as exc:  # e.g. a phrase on an index without positions
                print(f"error: {exc}", file=sys.stderr)
                return 2
            elapsed = (time.perf_counter() - start) * 1000
            print(f"\nquery:  {query}\nparsed: {parse(query)}")
            print(
                f"{searcher.last_match_count} matching documents, "
                f"top {len(results)} ({elapsed:.1f} ms):"
            )
            for rank, result in enumerate(results, start=1):
                print(f"{rank:>3}. {result}")
    if args.memory:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"\npeak memory: {peak / 2**20:.1f} MiB (tracemalloc)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
