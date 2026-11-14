"""The `findex` command, through typer's CliRunner (in-process) and one real subprocess."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findex.cli import app

runner = CliRunner()


@pytest.fixture
def built(tmp_path: Path, corpus_dir: Path) -> Path:
    out = tmp_path / "index.fidx"
    result = runner.invoke(
        app, ["index", str(corpus_dir), "--out", str(out), "--positions", "--executor", "serial"]
    )
    assert result.exit_code == 0, result.output
    return out


def test_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("index", "search", "stats"):
        assert command in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.startswith("findex ")


def test_index_reports_summary(built: Path) -> None:
    assert built.exists()


def test_index_default_executor_is_processes(tmp_path: Path, corpus_dir: Path) -> None:
    out = tmp_path / "index.fidx"
    result = runner.invoke(app, ["index", str(corpus_dir), "-o", str(out), "--workers", "2"])
    assert result.exit_code == 0, result.output
    assert "processes, 2 workers" in result.stdout


@pytest.mark.parametrize("fmt", ["binary", "pickle"])
def test_index_formats(tmp_path: Path, corpus_dir: Path, fmt: str) -> None:
    out = tmp_path / f"index.{fmt}"
    result = runner.invoke(
        app, ["index", str(corpus_dir), "-o", str(out), "--format", fmt, "--executor", "serial"]
    )
    assert result.exit_code == 0, result.output
    assert "documents" in result.stdout and "4" in result.stdout


def test_search_table(built: Path) -> None:
    result = runner.invoke(app, ["search", str(built), "whale NOT shark"])
    assert result.exit_code == 0, result.output
    assert "Whales" in result.stdout
    assert "1 of 4 match" in result.stdout


def test_search_json_is_clean_stdout(built: Path) -> None:
    result = runner.invoke(app, ["-vv", "search", str(built), "whale OR ship", "--json"])
    assert result.exit_code == 0, result.output
    hits = [json.loads(line) for line in result.stdout.splitlines()]  # every line is JSON
    assert [h["rank"] for h in hits] == [1, 2, 3]
    assert {"score", "doc_id", "title", "path", "snippet"} <= hits[0].keys()
    assert hits[0]["score"] >= hits[-1]["score"]
    assert "took" in result.stderr  # -vv: @timed DEBUG lines go to stderr, not stdout


def test_search_phrase_and_options(built: Path) -> None:
    result = runner.invoke(
        app,
        ["search", str(built), '"the whale"', "-k", "1", "--scorer", "tfidf", "--engine", "merge"],
    )
    assert result.exit_code == 0, result.output
    assert "Whales" in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["search", "missing.fidx", "whale"], "file not found"),
        (["search", "{index}", "whale AND"], "bad query"),
        (["search", "{index}", "(whale"], "bad query"),
        (["stats", "missing.fidx"], "file not found"),
        (["index", "no-such-dir", "--out", "x.fidx"], "corpus not found"),
        (["search", "{corpus}/doc0.txt", "whale"], "not a findex index"),
    ],
)
def test_expected_errors_are_one_line(
    built: Path, corpus_dir: Path, args: list[str], message: str
) -> None:
    args = [a.format(index=built, corpus=corpus_dir) for a in args]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert message in result.stderr
    assert "Traceback" not in result.output
    assert len(result.stderr.strip().splitlines()) == 1


def test_phrase_on_index_without_positions(tmp_path: Path, corpus_dir: Path) -> None:
    out = tmp_path / "plain.fidx"
    assert (
        runner.invoke(
            app, ["index", str(corpus_dir), "-o", str(out), "--executor", "threads"]
        ).exit_code
        == 0
    )
    result = runner.invoke(app, ["search", str(out), '"the whale"'])
    assert result.exit_code == 1
    assert "positions" in result.stderr


def test_stats_on_index_and_corpus(built: Path, corpus_dir: Path) -> None:
    on_index = runner.invoke(app, ["stats", str(built), "--top", "3"])
    on_corpus = runner.invoke(app, ["stats", str(corpus_dir), "--top", "3"])
    assert on_index.exit_code == on_corpus.exit_code == 0
    assert "the" in on_index.stdout  # most frequent term in the fixture corpus
    assert on_index.stdout == on_corpus.stdout  # same numbers both ways


@pytest.mark.slow
@pytest.mark.parametrize("fmt", ["pickle", "binary"])
def test_build_and_search_in_separate_processes(tmp_path: Path, corpus_dir: Path, fmt: str) -> None:
    """Regression for the Lab 2 `__main__` pickling trap: build in one process, load in another."""
    out = tmp_path / "index.bin"
    py = [sys.executable, "-m"]
    build = [*py, "findex.index", str(corpus_dir), "--out", str(out), "--format", fmt]
    subprocess.run(build, check=True, capture_output=True)
    search = [*py, "findex", "search", str(out), "whale NOT shark", "--json"]
    res = subprocess.run(search, check=True, capture_output=True, text=True, encoding="utf-8")
    hits = [json.loads(line) for line in res.stdout.splitlines()]
    assert [h["title"] for h in hits] == ["Whales"]
