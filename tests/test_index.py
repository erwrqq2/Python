import dataclasses

import pytest

from findex.index import DocMeta, Posting


def test_postings_sorted_with_tf(index):
    assert index.postings["whale"] == [Posting(0, 2), Posting(2, 1)]
    assert index.doc_ids("ship") == [0, 1, 2]
    for plist in index.postings.values():
        ids = [p.doc_id for p in plist]
        assert ids == sorted(ids) and len(ids) == len(set(ids))


def test_unknown_term(index):
    assert index.doc_ids("dragon") == []
    assert index.df("dragon") == 0


def test_doc_lengths_and_meta(index):
    # "Whales / The whale and the ship. The whale!" -> 8 tokens (title line included)
    assert index.doc_lengths[0] == 8
    assert index.doc_meta[2] == DocMeta(2, "doc2.txt", "Sharks")
    assert index.num_docs == 4
    assert not index.has_positions


def test_positions(index_pos):
    (p,) = [p for p in index_pos.postings["whale"] if p.doc_id == 0]
    assert p.tf == 2
    assert p.positions == (2, 7)  # whales=0 the=1 whale=2 and=3 the=4 ship=5 the=6 whale=7
    # positions do not change df/tf
    assert [(q.doc_id, q.tf) for q in index_pos.postings["ship"]] == [(0, 1), (1, 1), (2, 1)]


def test_posting_is_frozen_slotted_and_hashable():
    p = Posting(1, 3)
    assert {p, Posting(1, 3)} == {p}  # equal values -> same hash -> one element
    assert not hasattr(p, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.tf = 4  # type: ignore[misc]
    assert hash(DocMeta(0, "a", "b")) == hash(DocMeta(0, "a", "b"))
