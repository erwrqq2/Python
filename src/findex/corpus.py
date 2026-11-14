"""Lazy document stream: read the corpus one document at a time.

`iter_documents(root)` is a generator. Calling it does no I/O at all; each
`next()` opens exactly one file (or reads one JSON line), yields a `Document`
and forgets it. Memory therefore stays at "one document", whatever the
corpus size.

Supported sources:
  * a directory tree of ``*.txt`` files (found with ``Path.rglob``);
  * ``*.jsonl`` files, one JSON object per line with a ``"text"`` field;
  * ``root`` itself may be a single ``.txt`` / ``.jsonl`` file.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

TEXT_SUFFIXES = {".txt"}
JSONL_SUFFIXES = {".jsonl"}


class Document(NamedTuple):
    doc_id: int
    path: Path
    text: str


def read_text(path: Path) -> str:
    """Decode a file as UTF-8; on bad bytes log a warning and replace them."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        log.warning("%s: not valid UTF-8 (%s), undecodable bytes replaced", path, exc.reason)
        return data.decode("utf-8-sig", errors="replace")


def _iter_jsonl(path: Path) -> Iterator[str]:
    """Yield the "text" field of each line of a JSON-lines file, lazily."""
    with path.open(encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)["text"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                log.warning("%s:%d: skipped bad JSON line (%s)", path, lineno, exc)


def _iter_paths(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    # sorted() materialises only the *paths* (a few KB for 1000 files), not the
    # texts. It makes doc_id stable between runs, which the index in Lab 2 needs.
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in TEXT_SUFFIXES | JSONL_SUFFIXES:
            yield path


def iter_documents(root: Path) -> Iterator[Document]:
    """Yield the corpus under `root` one `Document` at a time.

    Unreadable files and bad JSON lines are logged and skipped; files with
    invalid UTF-8 are decoded with replacement characters — the run never
    crashes because of one bad document.
    """
    doc_id = 0
    for path in _iter_paths(Path(root)):
        if path.suffix in JSONL_SUFFIXES:
            for text in _iter_jsonl(path):
                yield Document(doc_id, path, text)
                doc_id += 1
            continue
        try:
            text = read_text(path)
        except OSError as exc:
            log.warning("%s: cannot read (%s), skipped", path, exc)
            continue
        yield Document(doc_id, path, text)
        doc_id += 1
