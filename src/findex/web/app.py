"""The findex web app: JSON API + HTML pages, one process, one index.

    INDEX_PATH=index.fidx findex serve --port 8000
    curl "http://127.0.0.1:8000/search?q=science+fiction&k=5"

Request path: uvicorn reads the socket and speaks ASGI -> Starlette routes the
request -> FastAPI validates the query parameters (Pydantic) and resolves the
dependencies -> our function runs -> the return value is validated against the
response model and serialised to JSON.

* The index is loaded once, in ``lifespan``, from ``Settings.index_path``; a bad
  path fails at start-up, not on the first query.
* Routes get the index through ``Depends(get_index)`` and friends, so tests can
  swap in a tiny index with ``app.dependency_overrides``.
* ``/search`` is a plain ``def``: scoring and snippets are CPU work, and FastAPI
  runs ``def`` routes in its thread pool, so a slow search cannot stall the event
  loop. (The ``async def`` variant was measured and lost — see README, Lab 7.)
* Every request gets an id (``X-Request-ID``, generated if absent). Unexpected
  exceptions are logged with it and answered with a JSON 500, no traceback.
"""

from __future__ import annotations

import logging
import math
import sys
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from findex.arrays import ArrayIndex, load_array_index
from findex.docstore import DocumentStore
from findex.query import QueryError, parse
from findex.rank import SearchResult
from findex.semantic import SemanticIndex
from findex.snippet import terms_regex
from findex.tokenize import tokenize
from findex.vector import STOPWORDS, Mode, VectorSearcher
from findex.web.schemas import Document, ErrorBody, Health, SearchHit, SearchResponse, Stats
from findex.web.settings import Settings, get_settings

log = logging.getLogger("findex.web")
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")

type ASGIScope = MutableMapping[str, Any]
type ASGIMessage = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[ASGIMessage]]
type Send = Callable[[ASGIMessage], Awaitable[None]]
type ASGIApp = Callable[[ASGIScope, Receive, Send], Awaitable[None]]


# --- request id -------------------------------------------------------------------------


class RequestIdMiddleware:
    """Pure ASGI middleware: put an id on every request and its response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        request_id = (
            headers.get(b"x-request-id", b"").decode("latin-1")[:64] or uuid.uuid4().hex[:12]
        )
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: ASGIMessage) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        await self.app(scope, receive, send_with_id)


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", "-"))


# --- dependencies -----------------------------------------------------------------------


def get_index(request: Request) -> ArrayIndex:
    index: ArrayIndex | None = getattr(request.app.state, "index", None)
    if index is None:
        raise HTTPException(status_code=503, detail="index is not loaded yet")
    return index


def get_searcher(
    request: Request, index: Annotated[ArrayIndex, Depends(get_index)]
) -> VectorSearcher:
    searcher: VectorSearcher | None = getattr(request.app.state, "searcher", None)
    return searcher if searcher is not None else VectorSearcher(index)


def get_store(request: Request, index: Annotated[ArrayIndex, Depends(get_index)]) -> DocumentStore:
    store: DocumentStore | None = getattr(request.app.state, "store", None)
    return store if store is not None else DocumentStore(index)


IndexDep = Annotated[ArrayIndex, Depends(get_index)]
SearcherDep = Annotated[VectorSearcher, Depends(get_searcher)]
StoreDep = Annotated[DocumentStore, Depends(get_store)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

Q = Annotated[str, Query(min_length=1, max_length=200, description="Query, e.g. 'whale NOT ship'")]
K = Annotated[int, Query(ge=1, le=50, description="Results per page")]
Page = Annotated[int, Query(ge=1, le=1000, description="Page number, from 1")]
ScorerParam = Literal["bm25", "tfidf"]
ModeParam = Literal["keyword", "semantic", "hybrid"]
SEMANTIC_POOL = 100  # semantic/hybrid rankings are cut at this depth


# --- the search itself (shared by the API and the HTML page) ----------------------------


def run_search(
    searcher: VectorSearcher, q: str, k: int, page: int, scorer: ScorerParam, mode: Mode = "keyword"
) -> tuple[list[SearchResult], int, float]:
    """One page of results and the total. Raises QueryError (keyword mode, broken query)
    and SemanticUnavailable (no embeddings on this server)."""
    start = time.perf_counter()
    if mode != "keyword" and searcher.semantic is None:
        raise SemanticUnavailable
    if mode == "keyword":
        tree = parse(q)
        results = searcher.search(tree, scorer, k=k * page, mode="keyword")
        # From the cached match, not from shared searcher state: def routes run in a
        # thread pool, so concurrent requests share the searcher.
        total = len(searcher.match(tree))
    else:
        results = searcher.search(q, scorer, k=SEMANTIC_POOL, mode=mode)
        total = len(results)
    terms = _query_words(q)
    if mode != "keyword":  # a natural-language query: don't highlight "a", "the", "of"...
        terms = [t for t in terms if t not in STOPWORDS] or terms
    page_results = [
        SearchResult(r.score, r.doc_id, r.title, searcher.snippet(r.doc_id, terms, q))
        for r in results[(page - 1) * k : page * k]
    ]
    return page_results, total, (time.perf_counter() - start) * 1000


class SemanticUnavailable(Exception):
    """This server has no embeddings: run `findex embed` (or use mode=keyword)."""


def _query_words(q: str) -> list[str]:
    try:
        return list(dict.fromkeys(parse(q).terms()))
    except QueryError:
        return list(dict.fromkeys(tokenize(q)))


def _terms(q: str) -> list[str]:
    return list(dict.fromkeys(parse(q).terms()))


# --- the app ----------------------------------------------------------------------------


def configure_logging(level: str) -> None:
    """Send findex's log records to stdout, where Docker and Render collect them.

    uvicorn only configures its own loggers; without this, records from `findex.*`
    reach Python's last-resort handler, which drops everything below WARNING. Done
    in the app (lifespan), not in the CLI: with ``--workers N`` every worker is a
    fresh process that imports the app but not the CLI's logging setup.
    """
    logger = logging.getLogger("findex")
    logger.setLevel(level)
    if not any(getattr(h, "_findex", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s [pid %(process)d] %(message)s")
        )
        handler._findex = True  # type: ignore[attr-defined]  # marker: added once per process
        logger.addHandler(handler)
        logger.propagate = False  # don't print twice if the root logger has a handler too


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        conf = settings or get_settings()
        configure_logging(conf.log_level)
        index = load_array_index(conf.index_path)  # a missing/broken file stops start-up here
        semantic = SemanticIndex.load(conf.index_path)  # None if `findex embed` was not run
        if semantic is not None:
            semantic.embed_query("warm up")  # load the ONNX model now, not on the first query
        app.state.index = index
        app.state.store = DocumentStore(index, conf.corpus_path)
        app.state.searcher = VectorSearcher(index, texts=app.state.store.text, semantic=semantic)
        app.state.started = time.monotonic()
        log.info("loaded %r from %s", index, conf.index_path)
        try:
            yield
        finally:
            app.state.index = None
            index.close()

    app = FastAPI(
        title="findex",
        summary="Full-text search over a small corpus (BM25 / TF-IDF, Boolean queries, phrases).",
        version=_version(),
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = request_id_of(request)
        log.error(
            "unhandled error request_id=%s %s %s",
            request_id,
            request.method,
            request.url.path,
            exc_info=exc,
        )
        body = ErrorBody(detail="internal server error", request_id=request_id)
        return JSONResponse(
            body.model_dump(), status_code=500, headers={"x-request-id": request_id}
        )

    # --- JSON API -----------------------------------------------------------------------

    @app.get("/search", response_model=SearchResponse, tags=["api"])
    def search(  # def, not async def: CPU-bound, FastAPI runs it in the thread pool
        searcher: SearcherDep,
        q: Q,
        k: K = 10,
        page: Page = 1,
        scorer: ScorerParam = "bm25",
        mode: ModeParam = "keyword",
    ) -> SearchResponse:
        """Ranked search; ``page`` and ``k`` select the slice of results.

        ``mode``: ``keyword`` (Boolean query + BM25/TF-IDF), ``semantic`` (embedding
        similarity, best passage per document) or ``hybrid`` (both, fused with RRF)."""
        try:
            results, total, took = run_search(searcher, q, k, page, scorer, mode)
        except QueryError as exc:
            raise HTTPException(status_code=422, detail=f"bad query: {exc}") from exc
        except SemanticUnavailable as exc:
            raise HTTPException(
                status_code=422, detail="semantic search is not set up here"
            ) from exc
        index = searcher.index
        hits = [
            SearchHit(
                doc_id=r.doc_id,
                title=r.title,
                score=round(r.score, 4),
                snippet=r.snippet,
                url=index.doc_meta[r.doc_id].path,
            )
            for r in results
        ]
        return SearchResponse(
            query=q,
            scorer=scorer,
            mode=mode,
            total=total,
            page=page,
            k=k,
            pages=max(1, math.ceil(total / k)),
            took_ms=round(took, 2),
            results=hits,
        )

    @app.get("/docs/{doc_id}", response_model=Document, tags=["api"])
    def document(doc_id: int, index: IndexDep, store: StoreDep) -> Document:
        """One document with its full text; 404 if there is no such doc_id."""
        meta = index.doc_meta.get(doc_id)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"no document {doc_id}")
        return Document(
            doc_id=doc_id,
            title=meta.title,
            url=meta.path,
            length=index.doc_length(doc_id),
            text=store.text(doc_id),
        )

    @app.get("/stats", response_model=Stats, tags=["api"])
    def stats(request: Request, index: IndexDep, searcher: SearcherDep) -> Stats:
        started = getattr(request.app.state, "started", time.monotonic())
        semantic = searcher.semantic
        return Stats(
            documents=index.num_docs,
            terms=len(index),
            postings=index.num_postings,
            tokens=index.num_tokens,
            avg_doc_length=round(index.avg_doc_length, 1),
            positions=index.has_positions,
            uptime_s=round(time.monotonic() - started, 1),
            passages=semantic.meta["chunks"] if semantic else None,
            embedding_model=semantic.meta["model"] if semantic else None,
        )

    @app.get("/health", response_model=Health, tags=["ops"], responses={503: {"model": ErrorBody}})
    async def health(request: Request) -> Health | JSONResponse:
        """200 when the index is loaded and answers; 503 otherwise (the platform restarts us).

        ``async def`` on purpose: it does no blocking work, so it answers straight from the
        event loop — even while every thread-pool thread is busy with searches."""
        index: ArrayIndex | None = getattr(request.app.state, "index", None)
        if index is None or index.closed:
            return JSONResponse({"detail": "index is not loaded"}, status_code=503)
        return Health(status="ok", documents=index.num_docs)

    # --- HTML pages ---------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(
        request: Request,
        searcher: SearcherDep,
        q: Annotated[str, Query(max_length=200)] = "",
        k: K = 10,
        page: Page = 1,
        scorer: ScorerParam = "bm25",
        mode: ModeParam = "keyword",
    ) -> HTMLResponse:
        context: dict[str, Any] = {
            "q": q,
            "k": k,
            "page": page,
            "scorer": scorer,
            "mode": mode,
            "semantic": searcher.semantic is not None,
            "results": [],
            "error": None,
            "index": searcher.index,
        }
        if q.strip():
            try:
                results, total, took = run_search(searcher, q, k, page, scorer, mode)
            except QueryError as exc:
                context["error"] = f"Не вдалося розібрати запит: {exc}"
            except SemanticUnavailable:
                context["error"] = "Семантичний пошук на цьому сервері не налаштований."
            else:
                context.update(
                    results=[(r, searcher.index.doc_meta[r.doc_id].path) for r in results],
                    total=total,
                    took=took,
                    pages=max(1, math.ceil(total / k)),
                )
        return templates.TemplateResponse(request, "index.html", context)

    @app.get("/doc/{doc_id}", response_class=HTMLResponse, include_in_schema=False)
    def doc_page(
        request: Request,
        doc_id: int,
        index: IndexDep,
        store: StoreDep,
        q: Annotated[str, Query(max_length=200)] = "",
    ) -> HTMLResponse:
        meta = index.doc_meta.get(doc_id)
        if meta is None:
            return templates.TemplateResponse(
                request, "missing.html", {"doc_id": doc_id, "q": q}, status_code=404
            )
        try:
            terms = _terms(q) if q.strip() else []
        except QueryError:
            terms = []
        text = store.text(doc_id) or ""
        if text.startswith(meta.title):  # the title is already the page heading
            text = text[len(meta.title) :].lstrip()
        context = {
            "meta": meta,
            "q": q,
            "length": index.doc_length(doc_id),
            "body": highlight_terms(text, terms),
        }
        return templates.TemplateResponse(request, "doc.html", context)

    return app


# --- template helpers -------------------------------------------------------------------


def highlight_snippet(snippet: str) -> Markup:
    """`**term**` markers from make_snippet -> <mark>, everything else escaped."""
    parts = str(escape(snippet)).split("**")
    return Markup("".join(f"<mark>{p}</mark>" if i % 2 else p for i, p in enumerate(parts)))


def highlight_terms(text: str, terms: list[str]) -> Markup:
    """Whole text with every occurrence of `terms` in <mark> (HTML-escaped)."""
    if not terms:
        return Markup(escape(text))
    pattern = terms_regex(terms)
    out: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        out.append(str(escape(text[cursor : match.start()])))
        out.append(f"<mark>{escape(match.group())}</mark>")
        cursor = match.end()
    out.append(str(escape(text[cursor:])))
    return Markup("".join(out))


templates.env.filters["highlight"] = highlight_snippet  # pyright: ignore[reportUnknownMemberType]


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("findex")
    except PackageNotFoundError:  # pragma: no cover
        return "dev"


app = create_app()
