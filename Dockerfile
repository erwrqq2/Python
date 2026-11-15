# syntax=docker/dockerfile:1
#
#   docker build -t findex .
#   docker run --rm -p 8000:8000 findex                       # index baked into the image
#   docker run --rm -p 8000:8000 -e INDEX_PATH=/data/my.fidx -v ./my:/data findex
#
# Configuration comes only from environment variables (INDEX_PATH, CORPUS_PATH,
# LOG_LEVEL, PORT); the defaults below point at the index built in stage 1.

# --- stage 1: dependencies from uv.lock, the project itself, and the index ----------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, in their own layer: it is rebuilt only when uv.lock changes.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Then the project, installed as a regular (non-editable) package.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# The searchable corpus (Lab 6 crawl of books.toscrape.com) -> index with positions.
COPY corpus ./corpus
RUN /app/.venv/bin/findex index corpus/books-toscrape.jsonl --out /app/index.fidx --positions

# Passage embeddings for semantic/hybrid search. The ONNX model (all-MiniLM-L6-v2,
# ~90 MB) is downloaded here, at build time, into /app/models: the running
# container never needs the network.
ENV FASTEMBED_CACHE_PATH=/app/models HF_HUB_DISABLE_PROGRESS_BARS=1
RUN /app/.venv/bin/findex embed /app/index.fidx --corpus corpus/books-toscrape.jsonl

# --- stage 2: slim runtime, no uv, no build cache, not root ---------------------------
FROM python:3.13-slim-bookworm
RUN useradd --create-home --uid 10001 findex
WORKDIR /app
COPY --from=build --chown=findex:findex /app/.venv /app/.venv
COPY --from=build --chown=findex:findex /app/index.fidx* /app/
COPY --from=build --chown=findex:findex /app/models /app/models
COPY --from=build --chown=findex:findex /app/corpus /app/corpus

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    INDEX_PATH=/app/index.fidx \
    CORPUS_PATH=/app/corpus/books-toscrape.jsonl \
    FASTEMBED_CACHE_PATH=/app/models \
    HF_HUB_OFFLINE=1 \
    LOG_LEVEL=INFO
USER findex
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=4)"

# findex serve reads $PORT (Render sets it); 0.0.0.0 so the port mapping can reach it.
CMD ["findex", "serve", "--host", "0.0.0.0"]
