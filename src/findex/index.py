"""Inverted index: term -> postings sorted by doc_id.

    uv run python -m findex.index data/ --out index.bin
    uv run python -m findex.index data/ --out index.fidx --format binary --positions

Built in ONE pass over the lazy document stream from Lab 1: for each document
we count its tokens in a small per-document Counter, then flush one Posting per
distinct term into a ``defaultdict(list)``. The corpus is never held in memory —
only the index grows.

Postings come out sorted by doc_id for free: ``iter_documents`` yields doc_ids
0, 1, 2, ... in increasing order and we only ever append.
"""

from __future__ import annotations

import argparse
import logging
import time
import tracemalloc
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from findex.corpus import Document, iter_documents
from findex.tokenize import tokenize

TITLE_MAX = 120


@dataclass(frozen=True, slots=True)
class Posting:
    """One (document, term) pair. Frozen + slots: hashable, no per-instance __dict__."""

    doc_id: int
    tf: int  # how many times the term occurs in this document
    positions: tuple[int, ...] = ()  # token offsets, only with --positions


@dataclass(frozen=True, slots=True)
class DocMeta:
    """What we show for a hit."""

    doc_id: int
    path: str
    title: str


@dataclass(slots=True)
class Index:
    postings: dict[str, list[Posting]]  # term -> postings sorted by doc_id
    doc_lengths: dict[int, int]  # doc_id -> number of tokens (for BM25 in Lab 3)
    doc_meta: dict[int, DocMeta]  # doc_id -> path/title
    has_positions: bool = False

    @property
    def num_docs(self) -> int:
        return len(self.doc_meta)

    @property
    def num_postings(self) -> int:
        return sum(map(len, self.postings.values()))

    def df(self, term: str) -> int:
        """Document frequency: in how many documents the term occurs."""
        return len(self.postings.get(term, ()))

    def doc_ids(self, term: str) -> list[int]:
        """Sorted doc_ids that contain `term` (empty list for unknown terms)."""
        return [p.doc_id for p in self.postings.get(term, ())]

    def all_doc_ids(self) -> list[int]:
        return sorted(self.doc_meta)


def title_of(doc: Document) -> str:
    """First line of the document (the downloader puts the book title there)."""
    end = doc.text.find("\n", 0, 4 * TITLE_MAX)  # bounded search, no copy of the text
    line = doc.text[: end if end != -1 else TITLE_MAX].strip()
    return line[:TITLE_MAX] or doc.path.stem


def build_index(docs: Iterable[Document], *, positions: bool = False) -> Index:
    """Build the inverted index in a single streaming pass over `docs`."""
    postings: defaultdict[str, list[Posting]] = defaultdict(list)
    doc_lengths: dict[int, int] = {}
    doc_meta: dict[int, DocMeta] = {}

    for doc in docs:
        if positions:
            where: defaultdict[str, list[int]] = defaultdict(list)
            length = 0
            for length, term in enumerate(tokenize(doc.text), start=1):
                where[term].append(length - 1)
            for term, offsets in where.items():
                postings[term].append(Posting(doc.doc_id, len(offsets), tuple(offsets)))
        else:
            counts = Counter(tokenize(doc.text))
            length = counts.total()
            for term, tf in counts.items():
                postings[term].append(Posting(doc.doc_id, tf))
        doc_lengths[doc.doc_id] = length
        doc_meta[doc.doc_id] = DocMeta(doc.doc_id, str(doc.path), title_of(doc))

    return Index(dict(postings), doc_lengths, doc_meta, positions)


def main(argv: list[str] | None = None) -> int:
    from findex.store import FORMATS, save  # local import: store imports this module

    parser = argparse.ArgumentParser(prog="python -m findex.index", description="Build an index.")
    parser.add_argument("root", type=Path, help="corpus directory or .txt/.jsonl file")
    parser.add_argument("--out", type=Path, required=True, help="where to save the index")
    parser.add_argument("--format", choices=FORMATS, default="pickle")
    parser.add_argument("--positions", action="store_true", help="store token positions")
    parser.add_argument("--limit", type=int, default=None, help="index only the first N docs")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if not args.root.exists():
        parser.error(f"{args.root} does not exist")

    tracemalloc.start()
    t0 = time.perf_counter()
    index = build_index(islice(iter_documents(args.root), args.limit), positions=args.positions)
    t1 = time.perf_counter()
    save(index, args.out, args.format)
    t2 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"documents:   {index.num_docs:,}")
    print(f"terms:       {len(index.postings):,}")
    print(f"postings:    {index.num_postings:,}")
    print(f"positions:   {'yes' if index.has_positions else 'no'}")
    print(f"build:       {t1 - t0:.2f} s")
    print(f"save:        {t2 - t1:.2f} s ({args.format})")
    print(f"file:        {args.out} ({args.out.stat().st_size / 2**20:.1f} MiB)")
    print(f"peak memory: {peak / 2**20:.1f} MiB (tracemalloc)")
    return 0


if __name__ == "__main__":
    # Under `python -m findex.index` this file runs as module `__main__`, so its classes
    # would be pickled as `__main__.Index` / `__main__.Posting` — and `findex.search`
    # could not unpickle them. Running main() from the importable module `findex.index`
    # makes the index use findex.index.Index, which any program can import.
    from findex.index import main as _main

    raise SystemExit(_main())
