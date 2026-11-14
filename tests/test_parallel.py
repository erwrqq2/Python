"""Parallel build: same index from every executor, chunking, failure propagation."""

import pickle
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from findex.corpus import iter_documents
from findex.index import Index, build_index
from findex.parallel import (
    Chunk,
    ExecutorName,
    build_parallel,
    build_partial,
    corpus_paths,
    make_chunks,
    merge,
)
from findex.store import save


def assert_same(a: Index, b: Index) -> None:
    assert a.postings == b.postings
    assert a.doc_lengths == b.doc_lengths
    assert a.doc_meta == b.doc_meta
    assert a.has_positions == b.has_positions


@pytest.mark.parametrize("positions", [False, True])
def test_serial_merge_of_one_partial_equals_lab4_build_index(
    corpus_dir: Path, positions: bool
) -> None:
    paths = corpus_paths(corpus_dir)
    serial = merge([build_partial(Chunk(0, tuple(paths)), positions)])
    assert_same(serial, build_index(iter_documents(corpus_dir), positions=positions))


@pytest.mark.parametrize("n_chunks", [1, 2, 3, 4, 10])
def test_any_chunking_merges_to_the_same_index(corpus_dir: Path, n_chunks: int) -> None:
    paths = corpus_paths(corpus_dir)
    chunks = make_chunks(paths, n_chunks)
    partials = [build_partial(c, positions=True) for c in reversed(chunks)]  # order-independent
    assert_same(merge(partials), build_index(iter_documents(corpus_dir), positions=True))


def test_make_chunks_contiguous_and_complete(tmp_path: Path) -> None:
    sizes = [1, 1, 1, 50, 1, 1, 1, 1]  # one big file must not sit with many others
    paths: list[Path] = []
    for i, size in enumerate(sizes):
        path = tmp_path / f"{i}.txt"
        path.write_text("x" * size, encoding="utf-8")
        paths.append(path)
    chunks = make_chunks(paths, 3)
    assert [p for c in chunks for p in c.paths] == paths  # contiguous, nothing lost
    starts = [sum(len(c.paths) for c in chunks[:i]) for i in range(len(chunks))]
    assert [c.first_doc_id for c in chunks] == starts  # doc_id ranges assigned up front
    big = next(c for c in chunks if paths[3] in c.paths)
    assert len(big.paths) <= 2  # the 50-byte file is (almost) a chunk of its own
    assert len(make_chunks(paths, 100)) <= len(paths)  # never more chunks than files
    assert len(make_chunks(paths, 1)) == 1


def test_partial_index_is_picklable_and_compact(corpus_dir: Path) -> None:
    partial = build_partial(Chunk(0, tuple(corpus_paths(corpus_dir))))
    restored = pickle.loads(pickle.dumps(partial))
    assert restored.postings == partial.postings
    assert all(arr.typecode == "I" for arr in partial.postings.values())


@pytest.mark.parametrize("executor", ["serial", "threads"])
def test_executors_build_identical_files(
    corpus_dir: Path, tmp_path: Path, executor: ExecutorName
) -> None:
    reference = build_index(iter_documents(corpus_dir), positions=True)
    index, stats = build_parallel(corpus_dir, executor=executor, workers=3, positions=True)
    assert_same(index, reference)
    assert stats.executor == executor and stats.merge_seconds <= stats.wall_seconds
    save(reference, tmp_path / "a.fidx", "binary")
    save(index, tmp_path / "b.fidx", "binary")
    assert (tmp_path / "a.fidx").read_bytes() == (tmp_path / "b.fidx").read_bytes()


@pytest.mark.slow
def test_processes_build_identical_files(corpus_dir: Path, tmp_path: Path) -> None:
    reference = build_index(iter_documents(corpus_dir), positions=True)
    done: list[int] = []
    index, stats = build_parallel(
        corpus_dir, executor="processes", workers=2, positions=True, on_chunk_done=done.append
    )
    assert sum(done) == 4  # every document reported to the progress callback
    assert stats.worker_cpu_seconds >= 0  # process_time ticks are ~16 ms on Windows
    save(reference, tmp_path / "a.fidx", "binary")
    save(index, tmp_path / "b.fidx", "binary")
    assert (tmp_path / "a.fidx").read_bytes() == (tmp_path / "b.fidx").read_bytes()


@pytest.mark.slow
def test_worker_failure_fails_the_build_with_its_traceback(corpus_dir: Path) -> None:
    paths = corpus_paths(corpus_dir)
    chunks = make_chunks(paths, 2)
    (corpus_dir / "doc2.txt").unlink()  # listed, then gone: read_text in the worker fails

    with pytest.raises(FileNotFoundError, match="doc2"):  # serial: plain exception
        merge([build_partial(Chunk(0, tuple(paths)))])

    def stale_chunks(paths: list[Path], n_chunks: int) -> list[Chunk]:
        return chunks  # cut before the file disappeared

    with pytest.MonkeyPatch.context() as mp:  # make_chunks runs in the parent process
        mp.setattr("findex.parallel.make_chunks", stale_chunks)
        with pytest.raises(FileNotFoundError, match="doc2") as info:
            build_parallel(corpus_dir, executor="processes", workers=2)
    remote = info.value.__cause__  # concurrent.futures attaches the worker's traceback
    assert remote is not None and "build_partial" in str(remote)


def test_jsonl_corpus_is_rejected_for_parallel_build(tmp_path: Path) -> None:
    (tmp_path / "docs.jsonl").write_text('{"text": "a"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON-lines"):
        corpus_paths(tmp_path)


words = st.sampled_from(["whale", "ship", "sea", "the", "київ", "don't"])


@settings(max_examples=30, deadline=None)
@given(
    texts=st.lists(st.lists(words, max_size=20).map(" ".join), min_size=1, max_size=8),
    n_chunks=st.integers(1, 8),
)
def test_property_parallel_equals_serial(texts: list[str], n_chunks: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for i, text in enumerate(texts):
            (root / f"{i:03}.txt").write_text(text, encoding="utf-8")
        partials = [build_partial(c, True) for c in make_chunks(corpus_paths(root), n_chunks)]
        assert_same(merge(partials), build_index(iter_documents(root), positions=True))
