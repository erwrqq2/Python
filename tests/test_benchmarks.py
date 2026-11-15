"""pytest-benchmark for the hot paths (Lab 8). In CI they run in their own job and are
compared with the previous run on main: a median more than 2x slower fails the build.

    uv run pytest tests/test_benchmarks.py --benchmark-only
"""

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from findex.arrays import ArrayIndex
from findex.corpus import Document, iter_documents
from findex.index import Index, build_index
from findex.query import parse
from findex.rank import SCORERS, Searcher
from findex.vector import VectorSearcher

WORDS = [
    "whale",
    "ship",
    "sea",
    "captain",
    "harpoon",
    "storm",
    "deck",
    "sail",
    "river",
    "island",
    "ocean",
    "wind",
]


def run[T](benchmark: BenchmarkFixture, func: Callable[[], T]) -> T:
    # pytest-benchmark's fixture is untyped; it returns whatever func returns.
    return cast(T, benchmark(func))


@pytest.fixture(scope="module")
def corpus() -> list[Document]:
    """A deterministic 300-document corpus, big enough for the timings to mean something."""
    docs: list[Document] = []
    for i in range(300):
        words = [WORDS[(i * 7 + j * j) % len(WORDS)] for j in range(120 + i % 50)]
        docs.append(Document(i, Path(f"d{i}.txt"), " ".join(words)))
    return docs


@pytest.fixture(scope="module")
def index(corpus: list[Document]) -> Index:
    return build_index(corpus)


def test_bench_build_index(benchmark: BenchmarkFixture, corpus: list[Document]) -> None:
    result = run(benchmark, lambda: build_index(corpus))
    assert result.num_docs == 300


def test_bench_search_lab3(benchmark: BenchmarkFixture, index: Index) -> None:
    searcher, tree = Searcher(index), parse("whale OR captain OR storm")
    results = run(benchmark, lambda: searcher.search(tree, SCORERS["bm25"], 10))
    assert len(results) == 10


def test_bench_search_numpy(benchmark: BenchmarkFixture, index: Index) -> None:
    searcher, tree = (
        VectorSearcher(ArrayIndex.from_index(index)),
        parse("whale OR captain OR storm"),
    )
    results = run(benchmark, lambda: searcher.search(tree, "bm25", 10))
    assert len(results) == 10


def test_bench_corpus_read(benchmark: BenchmarkFixture, tmp_path: Path) -> None:
    for i in range(50):
        (tmp_path / f"{i}.txt").write_text(" ".join(WORDS * 40), encoding="utf-8")
    docs = run(benchmark, lambda: list(iter_documents(tmp_path)))
    assert len(docs) == 50
