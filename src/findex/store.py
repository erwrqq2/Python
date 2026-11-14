"""Save and load an Index in two formats.

``pickle``
    One call each way, keeps every Python object as is.

    !!! Never ``load()`` a pickle you did not create yourself. Unpickling is not
    "reading data": the file is a small program for the pickle VM, and its
    REDUCE opcode calls any importable callable with arguments from the file —
    e.g. ``os.system("...")``. A crafted index.bin downloaded from the internet
    runs arbitrary code the moment it is loaded. Use it only for your own
    cache files.

``binary`` (own format, built on ``array('I')``)
    Only numbers and strings, so loading it can never execute code::

        b"FIDX\\x01"                      magic + version
        uint32 little-endian             length of the JSON header in bytes
        JSON header (UTF-8)              {"positions": bool,
                                          "docs":  [[doc_id, path, title, length], ...],
                                          "terms": [[term, offset, count, n_positions], ...]}
        data                             per term, at `offset` from the start of data:
                                           doc_ids    count       x uint32
                                           tfs        count       x uint32
                                           positions  n_positions x uint32 (if any;
                                                      split per posting by tf)

    The JSON header lets a later lab read one term's postings without parsing
    the rest (stretch goal: mmap).

``load(path)`` detects the format from the first bytes.
"""

from __future__ import annotations

import gc
import json
import pickle
import struct
import sys
from array import array
from collections.abc import Generator
from contextlib import contextmanager
from itertools import islice
from pathlib import Path
from typing import Final, Literal, TypedDict, cast

from findex.index import DocMeta, Index, Posting
from findex.timing import timed

type Format = Literal["pickle", "binary"]
FORMATS: Final[tuple[Format, ...]] = ("pickle", "binary")
MAGIC: Final = b"FIDX\x01"
_HEADER_LEN: Final = struct.Struct("<I")


class IndexFormatError(Exception):
    """The file exists but is not an index this version of findex can read."""


class BinaryHeader(TypedDict):
    """Shape of the JSON header of the binary format (lists in JSON, tuples here)."""

    positions: bool
    docs: list[tuple[int, str, str, int]]  # doc_id, path, title, length
    terms: list[tuple[str, int, int, int]]  # term, byte offset, count, n_positions


if array("I").itemsize != 4:  # pragma: no cover - true on all mainstream platforms
    raise ImportError("findex.store needs a 4-byte array('I')")


@timed
def save(index: Index, path: Path, fmt: Format = "pickle") -> None:
    path = Path(path)
    if fmt == "pickle":
        with path.open("wb") as f:
            pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)
    elif fmt == "binary":
        _save_binary(index, path)
    else:
        raise ValueError(f"unknown format {fmt!r}, expected one of {FORMATS}")


@contextmanager
def gc_paused() -> Generator[None]:
    """Loading creates ~1M objects and no garbage; every automatic GC pass would
    scan them all again for nothing. Pausing GC makes load ~30% faster."""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


@contextmanager
def open_index(path: Path) -> Generator[Index]:
    """``with open_index("index.bin") as ix: ...`` — load, use, always close.

    Everything before ``yield`` is ``__enter__``; the ``finally`` block is
    ``__exit__`` and runs whether the body finishes, returns or raises. An
    exception from the body is re-raised at the ``yield`` and propagates after
    ``close()`` — nothing is swallowed.
    """
    index = load(path)
    try:
        yield index
    finally:
        index.close()


@timed
def load(path: Path) -> Index:
    """Load an index saved by `save` (format detected from the file header)."""
    path = Path(path)
    data = path.read_bytes()  # FileNotFoundError propagates as is
    with gc_paused():
        if data.startswith(MAGIC):
            try:
                return _load_binary(data)
            except (struct.error, ValueError, KeyError, IndexError) as exc:
                raise IndexFormatError(f"{path}: damaged binary index") from exc
        # See the module docstring: only ever do this with files you created yourself.
        try:
            index: object = pickle.loads(data)  # noqa: S301
        except (pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError) as exc:
            raise IndexFormatError(f"{path}: not a findex index") from exc
    if not isinstance(index, Index):
        raise IndexFormatError(f"{path}: pickle does not contain a findex Index")
    return index


# --- binary format ------------------------------------------------------------------


def _le(arr: array[int]) -> array[int]:
    """Make the array little-endian in place (no-op on x86/ARM)."""
    if sys.byteorder == "big":  # pragma: no cover
        arr.byteswap()
    return arr


def _save_binary(index: Index, path: Path) -> None:
    terms = sorted(index.postings)
    header_terms: list[tuple[str, int, int, int]] = []
    offset = 0
    for term in terms:
        plist = index.postings[term]
        n_pos = sum(len(p.positions) for p in plist) if index.has_positions else 0
        header_terms.append((term, offset, len(plist), n_pos))
        offset += 4 * (2 * len(plist) + n_pos)
    header: BinaryHeader = {
        "positions": index.has_positions,
        "docs": [
            (m.doc_id, m.path, m.title, index.doc_lengths[m.doc_id])
            for m in index.doc_meta.values()
        ],
        "terms": header_terms,
    }
    raw_header = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    with path.open("wb") as f:
        f.write(MAGIC)
        f.write(_HEADER_LEN.pack(len(raw_header)))
        f.write(raw_header)
        for term in terms:  # same order as the offsets above
            plist = index.postings[term]
            _le(array("I", [p.doc_id for p in plist])).tofile(f)
            _le(array("I", [p.tf for p in plist])).tofile(f)
            if index.has_positions:
                _le(array("I", [x for p in plist for x in p.positions])).tofile(f)


def _uint32s(view: memoryview, start: int, count: int) -> array[int]:
    arr = array("I")
    arr.frombytes(view[start : start + 4 * count])
    return _le(arr)


def _load_binary(data: bytes) -> Index:
    pos = len(MAGIC)
    (header_len,) = _HEADER_LEN.unpack_from(data, pos)
    pos += _HEADER_LEN.size
    # json.loads returns Any; the file was written by _save_binary with this exact shape.
    header = cast(BinaryHeader, json.loads(data[pos : pos + header_len]))
    base = pos + header_len
    view = memoryview(data)

    postings: dict[str, list[Posting]] = {}
    for term, offset, count, n_pos in header["terms"]:
        start = base + offset
        doc_ids = _uint32s(view, start, count)
        tfs = _uint32s(view, start + 4 * count, count)
        if n_pos:
            flat = iter(_uint32s(view, start + 8 * count, n_pos))
            postings[term] = [
                Posting(d, t, tuple(islice(flat, t))) for d, t in zip(doc_ids, tfs, strict=True)
            ]
        else:
            postings[term] = [Posting(d, t) for d, t in zip(doc_ids, tfs, strict=True)]

    doc_meta = {d: DocMeta(d, p, t) for d, p, t, _ in header["docs"]}
    doc_lengths = {d: n for d, _, _, n in header["docs"]}
    return Index(postings, doc_lengths, doc_meta, header["positions"])
