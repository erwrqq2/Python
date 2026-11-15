"""Semantic search (Lab 8): passage embeddings, cosine similarity, best passage per document.

    findex embed index.fidx            # -> index.fidx.emb.npy, .chunks.npy, .emb.json

* Documents are split into overlapping passages of ~150 words (≈ 200 model tokens;
  all-MiniLM-L6-v2 reads at most 256), overlap 30 words — a sentence cut at a
  passage border is still whole in the neighbour.
* Every passage vector is L2-normalised once, so cosine similarity is a plain dot
  product: one ``E @ q`` scores all passages.
* A document's score is its *best* passage (``np.maximum.at``): a long book that
  mentions the topic in one chapter is not diluted by the other 300 passages.

The model runs through ONNX Runtime (fastembed) — no PyTorch, ~90 MB, so it fits
the free Render instance. Embedding the query is cached per text.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TypedDict, cast

import numpy as np
from numpy.typing import ArrayLike, NDArray

from findex.timing import timed

if TYPE_CHECKING:
    from fastembed import TextEmbedding

log = logging.getLogger(__name__)

DEFAULT_MODEL: Final = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_WORDS: Final = 150
OVERLAP: Final = 30

type FloatArray = NDArray[np.float32]


class EmbeddingMeta(TypedDict):
    model: str
    chunk_words: int
    overlap: int
    dim: int
    chunks: int
    docs: int


def paths_for(index_path: Path) -> tuple[Path, Path, Path]:
    """Embedding files that belong to an index file."""
    base = str(index_path)
    return Path(base + ".emb.npy"), Path(base + ".chunks.npy"), Path(base + ".emb.json")


def chunk_words(
    text: str, size: int = CHUNK_WORDS, overlap: int = OVERLAP
) -> Iterator[tuple[int, str]]:
    """(first word index, passage text) windows of `size` words, `overlap` words shared."""
    words = text.split()
    step = max(1, size - overlap)
    for start in range(0, max(len(words) - overlap, 1), step):
        yield start, " ".join(words[start : start + size])


@lru_cache(maxsize=2)
def load_model(name: str) -> TextEmbedding:
    from fastembed import TextEmbedding  # heavy import: only when embeddings are used

    log.info("loading embedding model %s", name)
    return TextEmbedding(name, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))


def _normalise(vectors: ArrayLike) -> FloatArray:
    v = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    return (v / np.maximum(norms, 1e-12)).astype(np.float32)


@dataclass
class SemanticIndex:
    vectors: FloatArray  # float32[C, d], unit length
    chunk_doc: NDArray[np.int32]  # int32[C]: doc_id of each passage
    chunk_start: NDArray[np.int32]  # int32[C]: first word of each passage in its document
    meta: EmbeddingMeta

    @cached_property
    def num_docs(self) -> int:
        return self.meta["docs"]

    def save(self, index_path: Path) -> None:
        emb, chunks, meta = paths_for(index_path)
        np.save(emb, self.vectors)
        np.save(chunks, np.stack([self.chunk_doc, self.chunk_start], axis=1))
        meta.write_text(json.dumps(self.meta, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, index_path: Path) -> SemanticIndex | None:
        """The embeddings saved next to `index_path`, or None if there are none."""
        emb, chunks, meta = paths_for(index_path)
        if not (emb.exists() and chunks.exists() and meta.exists()):
            return None
        pairs = np.load(chunks)
        # json.loads returns Any; the file was written by save() above.
        info = cast(EmbeddingMeta, json.loads(meta.read_text(encoding="utf-8")))
        vectors = np.load(emb, mmap_mode="r")  # memory-mapped: pages load on first use
        return cls(vectors, pairs[:, 0].astype(np.int32), pairs[:, 1].astype(np.int32), info)

    def embed_query(self, text: str) -> FloatArray:
        return _query_vector(self.meta["model"], text)

    @timed
    def search(self, text: str, k: int) -> list[tuple[int, float]]:
        """Top `k` documents by their best passage's cosine similarity to `text`."""
        from findex.vector import top_k

        sims = self.vectors @ self.embed_query(text)  # one dot product per passage
        best = np.full(self.num_docs, -np.inf, dtype=np.float64)
        np.maximum.at(best, self.chunk_doc, sims.astype(np.float64))
        candidates = np.flatnonzero(best > -np.inf).astype(np.int64)
        return top_k(candidates, best, k)

    def best_passage(self, doc_id: int, text: str, query: str) -> str:
        """The passage of `doc_id` closest to `query` (passed in, never shared state:
        searches run concurrently in the web app's thread pool)."""
        rows = np.flatnonzero(self.chunk_doc == doc_id)
        if not len(rows):
            return ""
        best_row = rows[int(np.argmax(self.vectors[rows] @ self.embed_query(query)))]
        words = text.split()
        start = int(self.chunk_start[best_row])
        return " ".join(words[start : start + self.meta["chunk_words"]])


@lru_cache(maxsize=512)
def _query_vector_cached(model: str, text: str) -> FloatArray:
    (vector,) = list(load_model(model).query_embed([text]))
    return _normalise(np.asarray(vector))[0] if np.ndim(vector) == 2 else _normalise(vector)


def _query_vector(model: str, text: str) -> FloatArray:
    return _query_vector_cached(model, text)


@timed
def build_embeddings(
    docs: Iterable[tuple[int, str]],
    num_docs: int,
    model: str = DEFAULT_MODEL,
    size: int = CHUNK_WORDS,
    overlap: int = OVERLAP,
    batch_size: int = 64,
    on_progress: Callable[[int], None] | None = None,
) -> SemanticIndex:
    """Embed every passage of `docs` ((doc_id, text) pairs)."""
    chunk_doc: list[int] = []
    chunk_start: list[int] = []
    passages: list[str] = []
    for doc_id, text in docs:
        for start, passage in chunk_words(text, size, overlap):
            chunk_doc.append(doc_id)
            chunk_start.append(start)
            passages.append(passage)
    encoder = load_model(model)
    vectors: list[NDArray[Any]] = []
    for i in range(0, len(passages), batch_size):
        batch = passages[i : i + batch_size]
        vectors.extend(np.asarray(v) for v in encoder.passage_embed(batch, batch_size=batch_size))
        if on_progress:
            on_progress(len(batch))
    matrix = _normalise(np.stack(vectors)) if vectors else np.zeros((0, 384), dtype=np.float32)
    meta: EmbeddingMeta = {
        "model": model,
        "chunk_words": size,
        "overlap": overlap,
        "dim": int(matrix.shape[1]),
        "chunks": len(passages),
        "docs": num_docs,
    }
    return SemanticIndex(
        matrix, np.asarray(chunk_doc, dtype=np.int32), np.asarray(chunk_start, dtype=np.int32), meta
    )
