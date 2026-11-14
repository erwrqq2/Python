"""Lab 1 statistics and the Lab 3 precision@5 evaluation, on the fixture corpus."""

import json
from itertools import islice
from pathlib import Path

import pytest

from findex.corpus import Document, iter_documents
from findex.evaluate import LabelledQuery, evaluate, format_table, load_queries
from findex.index import Index
from findex.stats import compute_stats, compute_stats_eager, format_report, limited
from findex.stats import main as stats_main


def test_lazy_and_eager_stats_agree(docs: list[Document]) -> None:
    lazy, eager = compute_stats(docs), compute_stats_eager(docs)
    assert (lazy.documents, lazy.tokens, lazy.vocabulary) == (4, 26, 18)  # 8 + 7 + 7 + 4 tokens
    assert (eager.documents, eager.tokens, eager.counts) == (
        lazy.documents,
        lazy.tokens,
        lazy.counts,
    )


def test_limited_uses_islice(corpus_dir: Path) -> None:
    assert len(list(limited(iter_documents(corpus_dir), 2))) == 2
    assert len(list(limited(iter_documents(corpus_dir), None))) == 4


def test_format_report(docs: list[Document]) -> None:
    report = format_report(compute_stats(docs), top=3, elapsed=0.5, peak=2**20, mode="lazy")
    assert "documents:   4" in report
    assert "1. the" in report
    assert "peak memory: 1.0 MiB" in report


def test_stats_module_cli(corpus_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert stats_main([str(corpus_dir), "--top", "2", "--eager"]) == 0
    out = capsys.readouterr().out
    assert "mode:        eager (lists)" in out and "tokens:      26" in out


def test_precision_at_5(index: Index, tmp_path: Path) -> None:
    queries: list[LabelledQuery] = [
        {"query": "whale", "relevant": ["doc0", "doc2"]},
        {"query": "unrelated", "relevant": ["doc0"]},  # doc0 does not contain it
    ]
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(queries), encoding="utf-8")
    scores = evaluate(index, load_queries(path))
    assert [s.precision("bm25") for s in scores] == [0.4, 0.0]  # 2 of top 5, then 0
    assert scores[0].best_possible == 0.4
    table = format_table(scores)
    assert "| `whale` | 2 | 0.4 | 0.4 | 0.4 |" in table
    assert "**0.20**" in table  # mean of 0.4 and 0.0


def test_first_documents_of_fixture(corpus_dir: Path) -> None:
    first = next(islice(iter_documents(corpus_dir), 1))
    assert first.path.name == "doc0.txt"
