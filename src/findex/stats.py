"""Corpus statistics in one lazy pass.

    uv run python -m findex.stats data/              # lazy (generators)
    uv run python -m findex.stats data/ --limit 50   # first 50 documents
    uv run python -m findex.stats data/ --eager      # list-based version, for comparison

Prints documents, tokens, vocabulary size, top-N terms, elapsed time and peak
memory measured with tracemalloc.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import tracemalloc
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from findex.corpus import Document, iter_documents
from findex.tokenize import tokenize


@dataclass
class CorpusStats:
    documents: int
    tokens: int
    counts: Counter[str]

    @property
    def vocabulary(self) -> int:
        return len(self.counts)


def compute_stats(docs: Iterable[Document]) -> CorpusStats:
    """Lazy version: one pass, only the current document and the Counter live."""
    counts: Counter[str] = Counter()
    documents = 0
    for doc in docs:
        documents += 1
        counts.update(tokenize(doc.text))
    return CorpusStats(documents, counts.total(), counts)


def compute_stats_eager(docs: Iterable[Document]) -> CorpusStats:
    """Eager version, written badly on purpose: everything is a list first."""
    all_docs = list(docs)  # every text in memory
    all_tokens = [list(tokenize(d.text)) for d in all_docs]  # every token too
    counts: Counter[str] = Counter()
    for tokens in all_tokens:
        counts.update(tokens)
    return CorpusStats(len(all_docs), counts.total(), counts)


def limited(docs: Iterator[Document], limit: int | None) -> Iterator[Document]:
    return islice(docs, limit) if limit is not None else docs


def format_report(stats: CorpusStats, top: int, elapsed: float, peak: int, mode: str) -> str:
    lines = [
        f"mode:        {mode}",
        f"documents:   {stats.documents:,}",
        f"tokens:      {stats.tokens:,}",
        f"vocabulary:  {stats.vocabulary:,}",
        f"top {top} terms:",
    ]
    for rank, (term, count) in enumerate(stats.counts.most_common(top), start=1):
        lines.append(f"  {rank:>3}. {term:<15} {count:>12,}")
    lines += [
        f"elapsed:     {elapsed:.2f} s",
        f"peak memory: {peak / 2**20:.1f} MiB (tracemalloc)",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m findex.stats", description=__doc__.split("\n")[0]
    )
    parser.add_argument("root", type=Path, help="corpus directory or .txt/.jsonl file")
    parser.add_argument("--limit", type=int, default=None, help="use only the first N documents")
    parser.add_argument("--top", type=int, default=50, help="how many top terms to print")
    parser.add_argument("--eager", action="store_true", help="run the list-based version instead")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if not args.root.exists():
        parser.error(f"{args.root} does not exist")

    compute = compute_stats_eager if args.eager else compute_stats
    tracemalloc.start()
    start = time.perf_counter()
    stats = compute(limited(iter_documents(args.root), args.limit))
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    mode = "eager (lists)" if args.eager else "lazy (generators)"
    sys.stdout.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
    print(format_report(stats, args.top, elapsed, peak, mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
