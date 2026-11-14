"""Parallel index build: map (`build_partial` per chunk) + reduce (`merge`).

    index, stats = build_parallel(Path("data"), executor="processes", workers=8)

* The corpus file list is cut into contiguous chunks of roughly equal *bytes*
  (one 4 MB book should not land in a chunk with 30 others). Each chunk gets its
  doc_id range up front — ``first_doc_id`` = position of its first file — so
  doc_ids are unique across the corpus without any coordination.
* Workers receive only paths and read the files themselves: sending document
  texts would pickle the whole corpus once more.
* `build_partial` returns postings as flat ``array('I')`` pairs — a few large
  byte buffers pickle far cheaper than ~1M small Posting objects.
* `merge` runs in the parent and is sequential: it is the part Amdahl's law
  is about.

The same two functions run under three executors: a plain loop (``serial``,
exactly ``merge([build_partial(all paths)])``), a ThreadPoolExecutor and a
ProcessPoolExecutor with the ``spawn`` start method. A failing worker fails the
build with the worker's traceback; pending chunks are cancelled, nothing hangs.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import time
from array import array
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import (
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Final, Literal

from findex.corpus import JSONL_SUFFIXES, Document, iter_paths, read_text
from findex.index import DocMeta, Index, Posting, title_of
from findex.store import gc_paused
from findex.tokenize import tokenize

log = logging.getLogger(__name__)

type ExecutorName = Literal["serial", "threads", "processes"]
EXECUTOR_NAMES: Final[tuple[ExecutorName, ...]] = ("serial", "threads", "processes")
CHUNKS_PER_WORKER: Final = 4  # more chunks than workers: a slow chunk does not idle the rest


@dataclass(frozen=True, slots=True)
class Chunk:
    """A contiguous run of corpus files and the doc_id of the first one."""

    first_doc_id: int
    paths: tuple[Path, ...]


@dataclass(slots=True)
class PartialIndex:
    """The index of one chunk, in a pickle-friendly shape."""

    first_doc_id: int
    has_positions: bool
    doc_meta: list[DocMeta]
    doc_lengths: list[int]  # parallel to doc_meta
    postings: dict[str, array[int]]  # term -> flat (doc_id, tf, doc_id, tf, ...)
    positions: dict[str, array[int]]  # term -> all positions, split per posting by tf
    cpu_seconds: float  # process CPU spent inside this call (meaningful in a worker process)


@dataclass(frozen=True, slots=True)
class BuildStats:
    executor: ExecutorName
    workers: int
    chunks: int
    wall_seconds: float  # whole build: map + merge
    merge_seconds: float
    parent_cpu_seconds: float  # all threads of the parent process
    worker_cpu_seconds: float  # sum of build_partial CPU in worker processes (0 otherwise)


def corpus_paths(root: Path, limit: int | None = None) -> list[Path]:
    """The .txt files of the corpus in doc_id order. JSON-lines corpora are not supported
    here (their documents are lines, not files) — use the serial `build_index` for them."""
    paths = list(islice(iter_paths(Path(root)), limit))
    if any(p.suffix in JSONL_SUFFIXES for p in paths):
        raise ValueError("parallel build needs a tree of .txt files, not JSON-lines")
    return paths


def make_chunks(paths: Sequence[Path], n_chunks: int) -> list[Chunk]:
    """Cut `paths` into at most `n_chunks` contiguous chunks of roughly equal total size."""
    n_chunks = max(1, min(n_chunks, len(paths)))
    sizes = [p.stat().st_size for p in paths]
    target = sum(sizes) / n_chunks
    chunks: list[Chunk] = []
    start, accumulated = 0, 0
    for i, size in enumerate(sizes):
        boundary = target * (len(chunks) + 1)
        # Cut *before* file i when its middle lies past the boundary: the cut lands at
        # the nearest file edge, so one big file is not glued to a run of small ones.
        if i > start and accumulated + size / 2 > boundary and len(chunks) < n_chunks - 1:
            chunks.append(Chunk(start, tuple(paths[start:i])))
            start = i
        accumulated += size
    if start < len(paths):
        chunks.append(Chunk(start, tuple(paths[start:])))
    return chunks


def build_partial(chunk: Chunk, positions: bool = False) -> PartialIndex:
    """Index one chunk. Module-level and argument-picklable, so a process can run it."""
    cpu_start = time.process_time()
    postings: defaultdict[str, array[int]] = defaultdict(lambda: array("I"))
    flat_positions: defaultdict[str, array[int]] = defaultdict(lambda: array("I"))
    doc_meta: list[DocMeta] = []
    doc_lengths: list[int] = []

    for offset, path in enumerate(chunk.paths):
        doc_id = chunk.first_doc_id + offset
        doc = Document(doc_id, path, read_text(path))  # an unreadable file fails the build
        if positions:
            where: defaultdict[str, list[int]] = defaultdict(list)
            length = 0
            for length, term in enumerate(tokenize(doc.text), start=1):
                where[term].append(length - 1)
            for term, offsets in where.items():
                postings[term].extend((doc_id, len(offsets)))
                flat_positions[term].extend(offsets)
        else:
            counts = Counter(tokenize(doc.text))
            length = counts.total()
            for term, tf in counts.items():
                postings[term].extend((doc_id, tf))
        doc_meta.append(DocMeta(doc_id, str(path), title_of(doc)))
        doc_lengths.append(length)

    # dict(...): a defaultdict with a lambda factory cannot be pickled back to the parent.
    return PartialIndex(
        chunk.first_doc_id,
        positions,
        doc_meta,
        doc_lengths,
        dict(postings),
        dict(flat_positions),
        time.process_time() - cpu_start,
    )


def merge(partials: Iterable[PartialIndex]) -> Index:
    """Combine chunk indexes. Chunks are contiguous doc_id ranges, so concatenating
    each term's postings in chunk order keeps them sorted — no sort needed."""
    parts = sorted(partials, key=lambda p: p.first_doc_id)
    has_positions = any(p.has_positions for p in parts)
    postings: dict[str, list[Posting]] = {}
    doc_lengths: dict[int, int] = {}
    doc_meta: dict[int, DocMeta] = {}
    with gc_paused():  # ~1M new objects and no garbage: GC passes would only rescan them
        for part in parts:
            for term, pairs in part.postings.items():
                plist = postings.get(term)
                if plist is None:
                    postings[term] = plist = []
                pair_iter = iter(pairs)
                if has_positions:
                    flat = iter(part.positions[term])
                    plist.extend(
                        Posting(d, tf, tuple(islice(flat, tf)))
                        for d, tf in zip(pair_iter, pair_iter, strict=True)
                    )
                else:
                    # map(f, it, it) takes consecutive (doc_id, tf) pairs; the loop runs in C.
                    plist.extend(map(Posting, pair_iter, pair_iter))
            for meta, length in zip(part.doc_meta, part.doc_lengths, strict=True):
                doc_meta[meta.doc_id] = meta
                doc_lengths[meta.doc_id] = length
    return Index(postings, doc_lengths, doc_meta, has_positions)


def _make_executor(executor: ExecutorName, workers: int) -> Executor:
    if executor == "threads":
        return ThreadPoolExecutor(max_workers=workers, thread_name_prefix="findex")
    # spawn: a fresh interpreter per worker, the same on Windows, macOS and Linux. fork would
    # copy a parent that may hold locks taken by other threads (and is no longer the default).
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))


def build_parallel(
    root: Path,
    *,
    executor: ExecutorName = "processes",
    workers: int | None = None,
    positions: bool = False,
    limit: int | None = None,
    on_chunk_done: Callable[[int], None] | None = None,
    before_shutdown: Callable[[], None] | None = None,
) -> tuple[Index, BuildStats]:
    """Build the index of the .txt corpus under `root` with the chosen executor.

    `on_chunk_done(n_docs)` is called as chunks finish (progress bars);
    `before_shutdown()` runs while worker processes are still alive (benchmarks
    sample their CPU time and memory there).
    """
    workers = workers or os.cpu_count() or 1
    paths = corpus_paths(root, limit)
    chunks = make_chunks(paths, 1 if executor == "serial" else workers * CHUNKS_PER_WORKER)
    workers = max(1, min(workers, len(chunks)))  # no idle workers: each costs a process start
    log.info(
        "%s: %d documents in %d chunks, %d workers", executor, len(paths), len(chunks), workers
    )

    wall_start, cpu_start = time.perf_counter(), time.process_time()
    if executor == "serial":
        partials: list[PartialIndex] = []
        for chunk in chunks:
            partials.append(build_partial(chunk, positions))
            if on_chunk_done:
                on_chunk_done(len(chunk.paths))
    else:
        partials = _run_pool(executor, workers, chunks, positions, on_chunk_done, before_shutdown)
    merge_start = time.perf_counter()
    index = merge(partials)
    end = time.perf_counter()

    worker_cpu = sum(p.cpu_seconds for p in partials) if executor == "processes" else 0.0
    stats = BuildStats(
        executor,
        1 if executor == "serial" else workers,
        len(chunks),
        end - wall_start,
        end - merge_start,
        time.process_time() - cpu_start,
        worker_cpu,
    )
    log.info("built in %.2f s (merge %.2f s)", stats.wall_seconds, stats.merge_seconds)
    return index, stats


def _run_pool(
    executor: ExecutorName,
    workers: int,
    chunks: list[Chunk],
    positions: bool,
    on_chunk_done: Callable[[int], None] | None,
    before_shutdown: Callable[[], None] | None,
) -> list[PartialIndex]:
    pool = _make_executor(executor, workers)
    try:
        futures: dict[Future[PartialIndex], int] = {
            pool.submit(build_partial, chunk, positions): i for i, chunk in enumerate(chunks)
        }
        results: list[PartialIndex | None] = [None] * len(chunks)
        for future in as_completed(futures):
            i = futures[future]
            results[i] = future.result()  # re-raises a worker's exception, with its traceback
            if on_chunk_done:
                on_chunk_done(len(chunks[i].paths))
        if before_shutdown:
            before_shutdown()
    except BaseException:
        # Do not wait for the remaining chunks of a build that has already failed.
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return [r for r in results if r is not None]
