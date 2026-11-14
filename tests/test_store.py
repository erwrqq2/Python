import pickle

import pytest

from findex.store import FORMATS, MAGIC, load, save


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("fixture", ["index", "index_pos"])
def test_round_trip(tmp_path, request, fmt, fixture):
    index = request.getfixturevalue(fixture)
    path = tmp_path / f"index.{fmt}"
    save(index, path, fmt)
    loaded = load(path)
    assert loaded.postings == index.postings
    assert loaded.doc_lengths == index.doc_lengths
    assert loaded.doc_meta == index.doc_meta
    assert loaded.has_positions == index.has_positions


def test_binary_has_magic_and_no_pickle(tmp_path, index):
    path = tmp_path / "index.fidx"
    save(index, path, "binary")
    assert path.read_bytes().startswith(MAGIC)


def test_load_rejects_foreign_pickle(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(pickle.dumps({"not": "an index"}))
    with pytest.raises(TypeError):
        load(path)


def test_unknown_format(tmp_path, index):
    with pytest.raises(ValueError):
        save(index, tmp_path / "x", "yaml")
