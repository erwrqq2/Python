"""NumPy postings (Lab 8): the index as flat int32 arrays instead of objects.

All postings live in ONE ``int32`` array, laid out per term exactly as in the
binary file format of Lab 2::

    ... | doc_ids (count) | tfs (count) | positions (n_pos) | next term ...

For each term only ``(word_offset, count, n_pos)`` is kept; ``arrays(term)``
returns two *views* (slices, no copy) into the big array. Loading a binary
index is therefore ``np.frombuffer`` over the file bytes — no Posting objects,
no per-term arrays — and the cost is mostly parsing the JSON header. Document
lengths are one ``int32[N]`` array, indexable by doc_id.

`ArrayIndex` satisfies `findex.index.QueryIndex`, so the Boolean query tree,
phrases and engines work on it unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import cached_property
from itertools import pairwise
from pathlib import Path
from typing import Final, cast

import numpy as np
from numpy.typing import NDArray

from findex.index import DocMeta, Index, Posting
from findex.store import HEADER_LEN, MAGIC, BinaryHeader, IndexFormatError, load
from findex.timing import timed

type Int32Array = NDArray[np.int32]
EMPTY: Final[Int32Array] = np.zeros(0, dtype=np.int32)


class ArrayIndex:
    def __init__(
        self,
        words: Int32Array,
        spans: dict[str, tuple[int, int, int]],
        doc_len: Int32Array,
        doc_meta: dict[int, DocMeta],
        has_positions: bool,
    ) -> None:
        if list(doc_meta) != list(range(len(doc_meta))):
            raise ValueError("ArrayIndex needs doc_ids 0..N-1 in order")
        self._words = words  # all postings, see module docstring
        self._spans = spans  # term -> (word offset, count, n_positions)
        self.doc_len = doc_len  # int32[N], document length in tokens
        self.doc_meta = doc_meta
        self._has_positions = has_positions
        self.closed = False

    # --- construction -------------------------------------------------------------------

    @classmethod
    def from_index(cls, index: Index) -> ArrayIndex:
        """Convert an object index (Lab 2–3) into flat arrays."""
        chunks: list[list[int]] = []
        spans: dict[str, tuple[int, int, int]] = {}
        offset = 0
        for term in sorted(index.postings):
            plist = index.postings[term]
            flat = [p.doc_id for p in plist] + [p.tf for p in plist]
            n_pos = 0
            if index.has_positions:
                positions = [x for p in plist for x in p.positions]
                flat += positions
                n_pos = len(positions)
            spans[term] = (offset, len(plist), n_pos)
            chunks.append(flat)
            offset += len(flat)
        words = np.fromiter((x for chunk in chunks for x in chunk), dtype=np.int32, count=offset)
        doc_len = np.array([index.doc_lengths[d] for d in sorted(index.doc_meta)], dtype=np.int32)
        meta = {d: index.doc_meta[d] for d in sorted(index.doc_meta)}
        return cls(words, spans, doc_len, meta, index.has_positions)

    # --- QueryIndex / Index-like API ----------------------------------------------------

    @property
    def has_positions(self) -> bool:
        return self._has_positions

    @property
    def num_docs(self) -> int:
        return len(self.doc_len)

    @cached_property
    def avg_doc_length(self) -> float:
        # Python int sum / N, exactly as Index.avg_doc_length, so BM25 matches to the bit.
        return int(self.doc_len.sum(dtype=np.int64)) / self.num_docs if self.num_docs else 0.0

    @property
    def num_postings(self) -> int:
        return sum(count for _, count, _ in self._spans.values())

    @property
    def num_tokens(self) -> int:
        return int(self.doc_len.sum(dtype=np.int64))

    def __len__(self) -> int:
        return len(self._spans)

    def __contains__(self, term: object) -> bool:
        return term in self._spans

    def __iter__(self) -> Iterator[str]:
        return iter(self._spans)

    def __repr__(self) -> str:
        flags = "".join(
            [", positions" if self.has_positions else "", ", closed" if self.closed else ""]
        )
        return f"ArrayIndex(terms={len(self):_}, docs={self.num_docs:_}{flags})"

    def df(self, term: str) -> int:
        span = self._spans.get(term)
        return span[1] if span else 0

    def arrays(self, term: str) -> tuple[Int32Array, Int32Array]:
        """(doc_ids, tfs) of `term` as int32 views; empty arrays for an unknown term."""
        span = self._spans.get(term)
        if span is None:
            return EMPTY, EMPTY
        offset, count, _ = span
        return self._words[offset : offset + count], self._words[
            offset + count : offset + 2 * count
        ]

    def positions(self, term: str) -> Int32Array:
        """All positions of `term`, posting after posting (split them by tf)."""
        span = self._spans.get(term)
        if span is None or not span[2]:
            return EMPTY
        offset, count, n_pos = span
        start = offset + 2 * count
        return self._words[start : start + n_pos]

    def doc_ids(self, term: str) -> list[int]:
        return self.arrays(term)[0].tolist()

    def all_doc_ids(self) -> list[int]:
        return list(range(self.num_docs))

    def postings_for(self, term: str) -> list[Posting]:
        """Posting objects for one term, built on demand (phrase queries need positions)."""
        ids, tfs = self.arrays(term)
        if not self.has_positions:
            return [Posting(d, t) for d, t in zip(ids.tolist(), tfs.tolist(), strict=True)]
        flat = self.positions(term).tolist()
        bounds = [0, *np.cumsum(tfs).tolist()]
        return [
            Posting(d, t, tuple(flat[a:b]))
            for d, t, (a, b) in zip(ids.tolist(), tfs.tolist(), pairwise(bounds), strict=True)
        ]

    def doc_length(self, doc_id: int) -> int:
        return int(self.doc_len[doc_id])

    def close(self) -> None:
        self._words, self._spans = EMPTY, {}
        self.doc_len, self.doc_meta = EMPTY, {}
        self.__dict__.pop("avg_doc_length", None)
        self.closed = True


@timed
def load_array_index(path: Path) -> ArrayIndex:
    """Load a binary index straight into NumPy views (a pickle is converted)."""
    path = Path(path)
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        return ArrayIndex.from_index(load(path))
    try:
        pos = len(MAGIC)
        (header_len,) = HEADER_LEN.unpack_from(data, pos)
        pos += HEADER_LEN.size
        # json.loads returns Any; the header was written by store._save_binary.
        header = cast(BinaryHeader, json.loads(data[pos : pos + header_len]))
        base = pos + header_len
        words = np.frombuffer(data, dtype="<i4", offset=base).astype(np.int32, copy=False)
        spans = {
            term: (offset // 4, count, n_pos) for term, offset, count, n_pos in header["terms"]
        }
        docs = sorted(header["docs"])
        doc_len = np.array([length for _, _, _, length in docs], dtype=np.int32)
        meta = {d: DocMeta(d, p, t) for d, p, t, _ in docs}
    except (ValueError, KeyError, IndexError) as exc:
        raise IndexFormatError(f"{path}: damaged binary index") from exc
    return ArrayIndex(words, spans, doc_len, meta, header["positions"])
