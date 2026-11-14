"""Memory study: three ways to store the same postings.

    uv run python -m findex.memstudy data/

1. ``list[PlainPosting]`` — ``@dataclass(frozen=True)``: every instance has a __dict__
2. ``list[Posting]``      — ``@dataclass(frozen=True, slots=True)``: fixed slots
3. ``(array('I'), array('I'))`` per term — doc_ids and tfs as raw 4-byte ints, no objects

Each variant is built from the same lazy stream under tracemalloc, pickled to a
file, and loaded back. Prints a Markdown table for the README.
"""

from __future__ import annotations

import argparse
import gc
import logging
import os
import pickle
import tempfile
import time
import tracemalloc
from array import array
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from findex.corpus import Document, iter_documents
from findex.index import Posting
from findex.tokenize import tokenize

log = logging.getLogger(__name__)

VARIANTS = {
    "dataclass": "`list[Posting]`, plain `@dataclass`",
    "slots": "`list[Posting]`, `slots=True`",
    "array": "`array('I')` pairs (doc_ids, tfs)",
}


@dataclass(frozen=True)
class PlainPosting:
    """Same fields as Posting, but without slots (instances carry a __dict__)."""

    doc_id: int
    tf: int
    positions: tuple[int, ...] = ()


def build_postings(docs: Iterable[Document], variant: str) -> dict[str, Any]:
    if variant == "array":
        arrays: defaultdict[str, tuple[array[int], array[int]]] = defaultdict(
            lambda: (array("I"), array("I"))
        )
        for doc in docs:
            for term, tf in Counter(tokenize(doc.text)).items():
                ids, tfs = arrays[term]
                ids.append(doc.doc_id)
                tfs.append(tf)
        return dict(arrays)

    cls = PlainPosting if variant == "dataclass" else Posting
    lists: defaultdict[str, list[Any]] = defaultdict(list)
    for doc in docs:
        for term, tf in Counter(tokenize(doc.text)).items():
            lists[term].append(cls(doc.doc_id, tf))
    return dict(lists)


def measure(root: Path, variant: str, limit: int | None) -> dict[str, float]:
    gc.collect()
    tracemalloc.start()
    base, _ = tracemalloc.get_traced_memory()
    t0 = time.perf_counter()
    postings = build_postings(islice(iter_documents(root), limit), variant)
    build_s = time.perf_counter() - t0
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    fd, name = tempfile.mkstemp(suffix=".pkl")
    os.close(fd)
    path = Path(name)
    try:
        with path.open("wb") as f:
            pickle.dump(postings, f, protocol=pickle.HIGHEST_PROTOCOL)
        size = path.stat().st_size
        del postings
        gc.collect()
        t0 = time.perf_counter()
        with path.open("rb") as f:
            pickle.load(f)  # our own temp file — safe
        load_s = time.perf_counter() - t0
    finally:
        path.unlink()
    return {
        "peak": (peak - base) / 2**20,
        "retained": (retained - base) / 2**20,
        "size": size / 2**20,
        "load": load_s,
        "build": build_s,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.memstudy", description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--variant", choices=list(VARIANTS), action="append")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")  # progress -> stderr
    rows: list[tuple[str, dict[str, float]]] = []
    for variant in args.variant or list(VARIANTS):
        m = measure(args.root, variant, args.limit)
        rows.append((variant, m))
        log.info("%s: %s", variant, m)

    print()
    print("| Postings representation | Peak memory (build) | Index file size | Load time |")
    print("|---|---|---|---|")
    for variant, m in rows:
        cells = f"{m['peak']:.1f} MiB | {m['size']:.1f} MiB | {m['load']:.2f} s"
        print(f"| {VARIANTS[variant]} | {cells} |")
    print()
    print("| Postings representation | Retained after build | Build time |")
    print("|---|---|---|")
    for variant, m in rows:
        print(f"| {VARIANTS[variant]} | {m['retained']:.1f} MiB | {m['build']:.1f} s |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
