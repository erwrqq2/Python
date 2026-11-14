import pickle
from pathlib import Path

import pytest

from findex.index import Index
from findex.store import FORMATS, MAGIC, Format, IndexFormatError, load, open_index, save


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("fixture", ["index", "index_pos"])
def test_round_trip(
    tmp_path: Path, request: pytest.FixtureRequest, fmt: Format, fixture: str
) -> None:
    index = request.getfixturevalue(fixture)
    path = tmp_path / f"index.{fmt}"
    save(index, path, fmt)
    loaded = load(path)
    assert loaded.postings == index.postings
    assert loaded.doc_lengths == index.doc_lengths
    assert loaded.doc_meta == index.doc_meta
    assert loaded.has_positions == index.has_positions


def test_binary_has_magic_and_no_pickle(tmp_path: Path, index: Index) -> None:
    path = tmp_path / "index.fidx"
    save(index, path, "binary")
    assert path.read_bytes().startswith(MAGIC)


def test_load_rejects_foreign_pickle(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(pickle.dumps({"not": "an index"}))
    with pytest.raises(IndexFormatError):
        load(path)


def test_unknown_format(tmp_path: Path, index: Index) -> None:
    with pytest.raises(ValueError):
        # The type checker rejects "yaml" (not a Format); this tests the runtime guard.
        save(index, tmp_path / "x", "yaml")  # pyright: ignore[reportArgumentType]


def test_open_index_closes_even_on_error(tmp_path: Path, index: Index) -> None:
    path = tmp_path / "index.bin"
    save(index, path)
    seen: list[Index] = []
    with pytest.raises(RuntimeError), open_index(path) as ix:
        seen.append(ix)
        raise RuntimeError("boom")
    assert seen[0].closed


def test_open_index_closes_on_normal_exit(tmp_path: Path, index: Index) -> None:
    path = tmp_path / "index.bin"
    save(index, path)
    with open_index(path) as ix:
        assert "whale" in ix
    assert ix.closed
