"""Web app tests: TestClient + dependency_overrides, no real index file."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import findex.cli
from findex.arrays import ArrayIndex
from findex.corpus import Document
from findex.docstore import DocumentStore
from findex.index import Index
from findex.store import save
from findex.vector import VectorSearcher
from findex.web.app import create_app, get_index, get_searcher, get_store
from findex.web.settings import Settings

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def app(index_pos: Index, docs: list[Document]) -> Iterator[FastAPI]:
    """An app whose dependencies point at the 4-document fixture index (lifespan not run)."""
    application = create_app(Settings(index_path=Path("does-not-matter.fidx")))
    texts = {d.doc_id: d.text for d in docs}
    arrays = ArrayIndex.from_index(index_pos)
    store = DocumentStore(arrays)
    store.text = texts.get  # type: ignore[method-assign]  # in-memory texts instead of files
    searcher = VectorSearcher(arrays, texts=texts.get)
    application.dependency_overrides[get_index] = lambda: arrays
    application.dependency_overrides[get_searcher] = lambda: searcher
    application.dependency_overrides[get_store] = lambda: store
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# --- /search ----------------------------------------------------------------------------


def test_search_success(client: TestClient) -> None:
    response = client.get("/search", params={"q": "whale", "k": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "whale" and body["total"] == 2 and body["page"] == 1
    assert [hit["doc_id"] for hit in body["results"]] == [0, 2]
    assert "**whale**" in body["results"][0]["snippet"].lower()
    assert body["results"][0]["score"] > body["results"][1]["score"]
    assert response.headers["x-request-id"]


def test_search_pagination(client: TestClient) -> None:
    first = client.get("/search", params={"q": "ship OR whale", "k": 2}).json()
    second = client.get("/search", params={"q": "ship OR whale", "k": 2, "page": 2}).json()
    assert first["total"] == second["total"] == 3 and first["pages"] == 2
    ids = [h["doc_id"] for h in first["results"] + second["results"]]
    assert len(ids) == len(set(ids)) == 3  # pages do not overlap


def test_search_tfidf_and_phrase(client: TestClient) -> None:
    body = client.get("/search", params={"q": '"the whale"', "scorer": "tfidf"}).json()
    assert body["scorer"] == "tfidf" and [h["doc_id"] for h in body["results"]] == [0]


@pytest.mark.parametrize(
    "params",
    [
        {"q": ""},  # too short
        {"q": "x" * 201},  # too long
        {"q": "whale", "k": 0},
        {"q": "whale", "k": 51},
        {"q": "whale", "k": "ten"},
        {"q": "whale", "page": 0},
        {"q": "whale", "scorer": "pagerank"},
        {},  # q missing
        {"q": "whale AND"},  # parses as a broken query
    ],
)
def test_search_validation_422(client: TestClient, params: dict[str, str | int]) -> None:
    response = client.get("/search", params=params)
    assert response.status_code == 422
    assert "detail" in response.json()


# --- /docs, /stats, /health -------------------------------------------------------------


def test_document(client: TestClient, docs: list[Document]) -> None:
    body = client.get("/docs/2").json()
    assert body == {
        "doc_id": 2,
        "title": "Sharks",
        "url": "doc2.txt",
        "length": 7,
        "text": docs[2].text,
    }


def test_document_404_and_422(client: TestClient) -> None:
    assert client.get("/docs/99").status_code == 404
    assert client.get("/docs/99").json()["detail"] == "no document 99"
    assert client.get("/docs/two").status_code == 422


def test_stats(client: TestClient) -> None:
    body = client.get("/stats").json()
    assert body["documents"] == 4 and body["positions"] is True and body["tokens"] == 26


def test_health_ok_when_loaded(app: FastAPI, index_pos: Index) -> None:
    app.state.index = ArrayIndex.from_index(index_pos)
    assert TestClient(app).get("/health").json() == {"status": "ok", "documents": 4}


def test_health_503_when_index_not_loaded() -> None:
    app = create_app(Settings(index_path=Path("missing.fidx")))  # lifespan never runs here
    client = TestClient(app)
    assert client.get("/health").status_code == 503
    assert client.get("/search", params={"q": "whale"}).status_code == 503  # get_index


# --- errors -----------------------------------------------------------------------------


def test_unexpected_error_is_a_clean_500_with_request_id(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> VectorSearcher:
        raise RuntimeError("disk on fire")

    app.dependency_overrides[get_searcher] = broken
    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="findex.web"):
        response = client.get("/search", params={"q": "whale"}, headers={"X-Request-ID": "abc123"})
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error", "request_id": "abc123"}
    assert "disk on fire" not in response.text and "Traceback" not in response.text
    assert "request_id=abc123" in caplog.text and "disk on fire" in caplog.text


# --- HTML pages -------------------------------------------------------------------------


def test_home_page_without_query(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "<form" in page.text and "4 документах" in page.text


def test_home_page_results_are_highlighted_and_escaped(client: TestClient) -> None:
    page = client.get("/", params={"q": "whale", "k": 1})
    assert page.status_code == 200
    assert "<mark>whale</mark>" in page.text.lower()
    assert 'href="/doc/0?q=whale"' in page.text
    assert "сторінка 1 з 2" in page.text  # pager
    assert "<script>" not in client.get("/", params={"q": "<script>"}).text  # escaped


def test_home_page_bad_query_message(client: TestClient) -> None:
    page = client.get("/", params={"q": "(whale"})
    assert page.status_code == 200 and "Не вдалося розібрати запит" in page.text


def test_document_page(client: TestClient) -> None:
    page = client.get("/doc/0", params={"q": "whale"})
    assert page.status_code == 200 and "<mark>whale</mark>" in page.text.lower()
    assert client.get("/doc/42").status_code == 404


def test_openapi_lists_the_api(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/search", "/docs/{doc_id}", "/stats", "/health"} <= set(paths)


# --- lifespan with a real (temporary) index file ----------------------------------------


def test_lifespan_loads_index_and_corpus_once(
    tmp_path: Path, index_pos: Index, docs: list[Document]
) -> None:
    index_file, corpus = tmp_path / "i.fidx", tmp_path / "c.jsonl"
    save(index_pos, index_file, "binary")
    corpus.write_text(
        "\n".join(json.dumps({"url": f"u{d.doc_id}", "text": d.text}) for d in docs),
        encoding="utf-8",
    )
    app = create_app(Settings(index_path=index_file, corpus_path=corpus))
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/docs/3").json()["text"] == docs[3].text  # from the JSON-lines corpus
        assert "**whale**" in client.get("/search", params={"q": "whale"}).text.lower()
    assert app.state.index is None  # closed on shutdown


def test_lifespan_fails_fast_on_a_bad_index_path(tmp_path: Path) -> None:
    app = create_app(Settings(index_path=tmp_path / "nope.fidx"))
    with pytest.raises(FileNotFoundError), TestClient(app):
        pass


# --- findex serve -----------------------------------------------------------------------


def test_serve_runs_uvicorn_with_the_app(
    tmp_path: Path, index_pos: Index, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_file = tmp_path / "i.fidx"
    save(index_pos, index_file, "binary")
    calls: list[dict[str, object]] = []

    def fake_run(target: str, **options: object) -> None:
        calls.append({"target": target, **options})

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.delenv("PORT", raising=False)
    result = CliRunner().invoke(
        findex.cli.app, ["serve", "--index", str(index_file), "--workers", "4"]
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["target"] == "findex.web:app"
    assert calls[0]["workers"] == 4 and calls[0]["port"] == 8000


def test_serve_without_index_is_a_one_line_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(findex.cli.app, ["serve", "--index", str(tmp_path / "x.fidx")])
    assert result.exit_code == 1 and "index not found" in result.stderr
