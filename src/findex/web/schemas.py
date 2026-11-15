"""Pydantic models — only at the HTTP boundary. Inside, the Lab 2–3 dataclasses stay."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    doc_id: int
    title: str
    score: float
    snippet: str = Field(description="Best ±80-character window; matched terms wrapped in **")
    url: str = Field(description="Source of the document: page URL or corpus file")


class SearchResponse(BaseModel):
    query: str
    scorer: Literal["bm25", "tfidf"]
    mode: Literal["keyword", "semantic", "hybrid"] = "keyword"
    total: int = Field(description="How many documents match the Boolean query")
    page: int
    k: int
    pages: int
    took_ms: float
    results: list[SearchHit]


class Document(BaseModel):
    doc_id: int
    title: str
    url: str
    length: int = Field(description="Number of tokens")
    text: str | None = Field(description="Full text, if the corpus is available")


class Stats(BaseModel):
    documents: int
    terms: int
    postings: int
    tokens: int
    avg_doc_length: float
    positions: bool
    uptime_s: float
    passages: int | None = Field(default=None, description="Embedded passages (semantic search)")
    embedding_model: str | None = None


class Health(BaseModel):
    status: Literal["ok"]
    documents: int


class ErrorBody(BaseModel):
    detail: str
    request_id: str | None = None
