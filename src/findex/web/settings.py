"""Configuration from the environment (or a local `.env` file), never from code.

    INDEX_PATH=/app/index.fidx              # required in production
    CORPUS_PATH=/app/corpus/books.jsonl     # texts for snippets of JSON-lines corpora
    LOG_LEVEL=INFO

The same Docker image runs locally and on Render; only these variables differ.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    index_path: Path = Path("index.fidx")
    corpus_path: Path | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    page_size_max: int = 50


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
