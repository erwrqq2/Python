"""Document texts for snippets and the document view.

* ``.txt`` corpora: the index stores the file path, the text is read from there.
* JSON-lines corpora (the crawl): the index stores the page URL, so the text
  comes from the JSON-lines file given in ``CORPUS_PATH``. At start-up we only
  record the byte offset of each record (doc_ids are assigned in file order by
  `iter_documents`), then read one line on demand — the texts never sit in
  memory all at once.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from findex.corpus import read_text
from findex.index import Index

log = logging.getLogger(__name__)


class DocumentStore:
    def __init__(self, index: Index, corpus_path: Path | None = None) -> None:
        self.index = index
        self.corpus_path = corpus_path
        self._offsets: list[int] = []
        if corpus_path is not None and corpus_path.suffix == ".jsonl":
            self._offsets = self._scan(corpus_path)
            if len(self._offsets) != index.num_docs:
                log.warning(
                    "%s has %d records but the index has %d documents",
                    corpus_path,
                    len(self._offsets),
                    index.num_docs,
                )

    @staticmethod
    def _scan(path: Path) -> list[int]:
        """Byte offsets of the records `iter_documents` turns into documents."""
        offsets: list[int] = []
        with path.open("rb") as f:
            offset = 0
            for line in f:
                if line.strip():
                    try:
                        if "text" in json.loads(line):
                            offsets.append(offset)
                    except json.JSONDecodeError:
                        pass  # iter_documents skips it too, so it gets no doc_id
                offset += len(line)
        return offsets

    def text(self, doc_id: int) -> str | None:
        """Full text of a document (title first), or None if it is not available."""
        meta = self.index.doc_meta.get(doc_id)
        if meta is None:
            return None
        if self._offsets and self.corpus_path is not None and doc_id < len(self._offsets):
            with self.corpus_path.open("rb") as f:
                f.seek(self._offsets[doc_id])
                record = json.loads(f.readline())
            title, body = record.get("title"), record["text"]
            return f"{title}\n\n{body}" if title else body
        path = Path(meta.path)
        if path.suffix == ".txt" and path.exists():
            return read_text(path)
        return None
