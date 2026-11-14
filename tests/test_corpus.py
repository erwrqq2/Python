"""iter_documents checks: laziness, sources, bad input."""

import inspect
import json
import logging
from pathlib import Path

import pytest

from findex.corpus import Document, iter_documents


def test_is_generator(tmp_path: Path) -> None:
    assert inspect.isgeneratorfunction(iter_documents)
    # Calling it on a path that does not exist does no I/O yet — nothing fails
    # until next() is called.
    gen = iter_documents(tmp_path / "missing")
    assert inspect.isgenerator(gen)


def test_reads_txt_tree_in_stable_order(tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")
    (tmp_path / "b" / "c.txt").write_text("second", encoding="utf-8")
    (tmp_path / "ignored.csv").write_text("x", encoding="utf-8")
    docs = list(iter_documents(tmp_path))
    assert [d.text for d in docs] == ["first", "second"]
    assert [d.doc_id for d in docs] == [0, 1]
    assert isinstance(docs[0], Document)


def test_reads_jsonl_and_skips_bad_lines(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "docs.jsonl"
    lines = [json.dumps({"text": "one"}), "{not json", "", json.dumps({"text": "two"})]
    path.write_text("\n".join(lines), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        texts = [d.text for d in iter_documents(path)]
    assert texts == ["one", "two"]
    assert "bad JSON" in caplog.text


def test_bad_encoding_is_logged_not_fatal(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "bad.txt").write_bytes("Привіт".encode("cp1251"))
    (tmp_path / "good.txt").write_text("ok", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        docs = list(iter_documents(tmp_path))
    assert len(docs) == 2
    assert "\ufffd" in docs[0].text
    assert "not valid UTF-8" in caplog.text
