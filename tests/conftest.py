from pathlib import Path

import pytest

from findex.corpus import Document
from findex.index import Index, build_index

TEXTS = [
    "Whales\nThe whale and the ship. The whale!",  # 0
    "Ships\nA ship sails; no whales here.",  # 1
    "Sharks\nShark meets whale near the ship.",  # 2
    "Nothing\nCompletely unrelated text.",  # 3
]


def make_docs() -> list[Document]:
    return [Document(i, Path(f"doc{i}.txt"), text) for i, text in enumerate(TEXTS)]


@pytest.fixture
def index() -> Index:
    return build_index(make_docs())


@pytest.fixture
def index_pos() -> Index:
    return build_index(make_docs(), positions=True)


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    for i, text in enumerate(TEXTS):
        (root / f"doc{i}.txt").write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def docs() -> list[Document]:
    return make_docs()
