"""End to end: build with `python -m findex.index`, search with `python -m findex.search`.

Regression test for the `__main__` pickling trap: the index is built in one
process and loaded in another.
"""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("fmt", ["pickle", "binary"])
def test_index_then_search(tmp_path, corpus_dir, fmt):
    out = tmp_path / "index.bin"
    py = [sys.executable, "-m"]
    build = [*py, "findex.index", str(corpus_dir), "--out", str(out), "--format", fmt]
    subprocess.run(build, check=True, capture_output=True)
    search = [*py, "findex.search", str(out), "whale NOT shark", "--memory"]
    res = subprocess.run(search, check=True, capture_output=True, text=True, cwd=corpus_dir.parent)
    assert "1 matching documents" in res.stdout
    assert "[   0] Whales" in res.stdout
    assert "peak memory" in res.stdout
