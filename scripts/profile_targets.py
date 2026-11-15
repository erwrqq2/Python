"""Workloads for the profilers (Lab 8): the same work as `findex index` / `findex search`,
without the CLI around it, so Scalene and py-spy see only findex code.

    python scripts/profile_targets.py index  data/gutenberg
    python scripts/profile_targets.py search index.fidx "river ship captain"
    scalene run --cpu-only -o docs/profiling/scalene-search.json \\
        scripts/profile_targets.py --- search index.fidx "river ship captain"
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from findex.corpus import iter_documents
from findex.index import build_index
from findex.rank import Searcher, SearchResult
from findex.store import load


def run_index(root: str) -> None:
    start = time.perf_counter()
    index = build_index(iter_documents(Path(root)))
    print(
        f"indexed {index.num_docs} docs, {len(index)} terms in {time.perf_counter() - start:.1f} s"
    )


def run_search(index_path: str, query: str, repeat: int = 200) -> None:
    start = time.perf_counter()
    index = load(Path(index_path))
    loaded = time.perf_counter()
    searcher = Searcher(index)
    results: list[SearchResult] = []
    for _ in range(repeat):  # many searches, so scoring shows up next to loading
        results = searcher.search(query, k=10, snippets=False)
    done = time.perf_counter()
    print(
        f"load {loaded - start:.2f} s; {repeat} searches {done - loaded:.2f} s; top: {results[:1]}"
    )


if __name__ == "__main__":
    what, *args = sys.argv[1:]
    if what == "index":
        run_index(args[0])
    else:
        run_search(args[0], args[1])
