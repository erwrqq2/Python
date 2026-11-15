"""Crawler tests. All HTTP goes through httpx.MockTransport — no network."""

import asyncio
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import findex.cli
from findex.corpus import iter_documents
from findex.crawler import CrawlConfig, CrawlStats, FetchResult, RobotsCache, crawl, fetch
from findex.crawler.fetch import backoff_delay, parse_retry_after
from findex.crawler.urls import canonicalize, parse_html

type Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def run[T](coro: Awaitable[T]) -> T:
    async def main() -> T:
        return await coro

    return asyncio.run(main())


def client_for(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


class Sleeps:
    """A fake asyncio.sleep that records the requested delays."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def scripted(*responses: httpx.Response | Exception) -> tuple[Handler, list[httpx.Request]]:
    """A handler that returns (or raises) the given responses in order."""
    seen: list[httpx.Request] = []
    queue = list(responses)

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler, seen


# --- fetch ------------------------------------------------------------------------------


def test_fetch_records_status_bytes_and_time() -> None:
    handler, _ = scripted(httpx.Response(200, html="<p>hi</p>"))

    async def go() -> FetchResult:
        async with client_for(handler) as client:
            return await fetch(client, "https://a.test/")

    result = run(go())
    assert (result.status, result.attempts, result.error) == (200, 1, None)
    assert result.body == b"<p>hi</p>" and result.is_html and result.elapsed >= 0


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_retryable_status_then_success(status: int) -> None:
    handler, requests = scripted(httpx.Response(status), httpx.Response(200, text="ok"))
    sleeps = Sleeps()

    async def go() -> FetchResult:
        async with client_for(handler) as client:
            return await fetch(client, "https://a.test/", sleep=sleeps, rng=lambda: 0.5)

    result = run(go())
    assert result.status == 200 and result.attempts == 2 and len(requests) == 2
    assert sleeps.delays == [0.5 + 0.5]  # base 0.5 * 2**0 + jitter 1.0 * rng 0.5


def test_gives_up_after_three_attempts_with_exponential_backoff() -> None:
    handler, requests = scripted(*(httpx.Response(503) for _ in range(3)))
    sleeps = Sleeps()

    async def go() -> FetchResult:
        async with client_for(handler) as client:
            return await fetch(client, "https://a.test/", sleep=sleeps, rng=lambda: 0.0)

    result = run(go())
    assert (result.status, result.attempts, result.error) == (503, 3, "HTTP 503")
    assert len(requests) == 3
    assert sleeps.delays == [0.5, 1.0]  # 0.5 * 2**0, 0.5 * 2**1, no jitter


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_other_4xx_are_not_retried(status: int) -> None:
    handler, requests = scripted(httpx.Response(status))
    sleeps = Sleeps()

    async def go() -> FetchResult:
        async with client_for(handler) as client:
            return await fetch(client, "https://a.test/", sleep=sleeps)

    result = run(go())
    assert result.status == status and result.attempts == 1 and len(requests) == 1
    assert sleeps.delays == [] and not result.ok


def test_429_honours_retry_after_seconds() -> None:
    handler, _ = scripted(
        httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, text="ok")
    )
    sleeps = Sleeps()

    async def go() -> FetchResult:
        async with client_for(handler) as client:
            return await fetch(client, "https://a.test/", sleep=sleeps, rng=lambda: 0.0)

    assert run(go()).status == 200
    assert sleeps.delays == [7.0]  # Retry-After beats the 0.5 s backoff


def test_retry_after_parsing() -> None:
    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after(format_datetime(now + timedelta(seconds=30), usegmt=True), now) == 30.0
    assert parse_retry_after(format_datetime(now - timedelta(hours=1), usegmt=True), now) == 0.0
    assert parse_retry_after("soon") is None and parse_retry_after(None) is None


def test_backoff_is_capped() -> None:
    assert backoff_delay(10, base=0.5, cap=8.0, jitter=1.0, rng=lambda: 1.0) == 9.0


def test_timeout_is_retried_then_reported() -> None:
    calls = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return httpx.Response(200)

    async def go() -> FetchResult:
        async with client_for(slow) as client:
            return await fetch(client, "https://a.test/", timeout=0.02, sleep=Sleeps())

    result = run(go())
    assert result.status is None and result.attempts == 3 and calls == 3
    assert result.error is not None and result.error.startswith("timeout")


def test_connection_errors_are_retried() -> None:
    handler, _ = scripted(httpx.ConnectError("refused"), httpx.Response(200, text="ok"))

    async def go() -> FetchResult:
        async with client_for(handler) as client:
            return await fetch(client, "https://a.test/", sleep=Sleeps())

    assert run(go()).attempts == 2


def test_redirect_loop_is_not_retried() -> None:
    requests: list[httpx.Request] = []

    async def loop(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        target = "/b" if request.url.path == "/a" else "/a"
        return httpx.Response(302, headers={"Location": target})

    async def go() -> FetchResult:
        async with client_for(loop) as client:
            client.max_redirects = 5
            return await fetch(client, "https://a.test/a", sleep=Sleeps())

    result = run(go())
    assert result.status is None and result.attempts == 1
    assert result.error is not None and "redirect" in result.error
    assert len(requests) == 6  # the first request + 5 redirects, once


def test_cancellation_is_not_swallowed() -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200)

    async def go() -> None:
        async with client_for(hang) as client:
            task = asyncio.create_task(fetch(client, "https://a.test/"))
            await asyncio.sleep(0.01)
            task.cancel()
            await task

    with pytest.raises(asyncio.CancelledError):
        run(go())


# --- robots.txt -------------------------------------------------------------------------


def robots_site(robots: dict[str, httpx.Response]) -> tuple[Handler, Counter[str]]:
    hits: Counter[str] = Counter()

    async def handler(request: httpx.Request) -> httpx.Response:
        hits[request.url.host] += 1
        await asyncio.sleep(0.01)  # let concurrent callers pile up on the lock
        return robots[request.url.host]

    return handler, hits


def test_robots_rules_obeyed_and_cached_per_host() -> None:
    handler, hits = robots_site(
        {
            "a.test": httpx.Response(200, text="User-agent: *\nDisallow: /private\n"),
            "b.test": httpx.Response(404),
            "c.test": httpx.Response(503),
        }
    )

    async def go() -> list[bool]:
        async with client_for(handler) as client:
            robots = RobotsCache(client, fetcher=lambda url: fetch(client, url, sleep=Sleeps()))
            urls = ["https://a.test/x", "https://a.test/private/y", "https://a.test/z"]
            urls += ["https://b.test/private", "https://c.test/anything"]
            first = await asyncio.gather(*(robots.allowed(u) for u in urls))  # concurrent
            again = [await robots.allowed(u) for u in urls]
            assert first == again
            assert robots.fetches == 3  # one robots.txt download per host, cached
            return first

    assert run(go()) == [True, False, True, True, False]  # 404 -> allow all, 5xx -> none
    assert hits == {"a.test": 1, "b.test": 1, "c.test": 3}  # 503 retried inside one download


# --- URLs and HTML ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTP://Example.COM", "http://example.com/"),
        ("https://example.com:443/a/", "https://example.com/a"),
        ("http://example.com:8080/a", "http://example.com:8080/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/s?b=2&a=1&a=0", "https://example.com/s?a=0&a=1&b=2"),
        ("https://example.com/a/./b/../c", "https://example.com/a/c"),
        ("mailto:x@example.com", None),
        ("ftp://example.com/f", None),
        ("http://[::1", None),
    ],
)
def test_canonicalize(url: str, expected: str | None) -> None:
    assert canonicalize(url) == expected


def test_canonicalize_relative() -> None:
    assert canonicalize("../b.html#x", "https://s.test/dir/a.html") == "https://s.test/b.html"


def test_parse_html() -> None:
    html = """<html><head><title> A  Book </title><script>var x = 1;</script></head>
    <body><nav><a href="/">Home</a></nav>
    <article><h1>Whale</h1><p>The <b>great</b> whale.</p><a href="next.html#top">next</a>
    <a href="mailto:a@b.c">mail</a><a href="/">home again</a></article></body></html>"""
    page = parse_html(html, "https://s.test/books/one.html")
    assert page.title == "A Book"
    assert page.text == "Whale The great whale. next mail home again"  # no nav, no script
    assert page.links == ["https://s.test/", "https://s.test/books/next.html"]


# --- crawl ------------------------------------------------------------------------------


def fake_site(
    n_pages: int, *, latency: float = 0.0
) -> tuple[httpx.MockTransport, Counter[str], dict[str, int]]:
    """A small site: / links to /p1..pN; every page links around, with traps."""
    requests: Counter[str] = Counter()
    peak = {"now": 0, "max": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        key = f"{url.host}{url.path}" + (f"?{url.query.decode()}" if url.query else "")
        requests[key] += 1
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        try:
            await asyncio.sleep(latency)
            if url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
            if url.path == "/broken":
                return httpx.Response(500)
            if url.path == "/image.png":
                return httpx.Response(
                    200, content=b"\x89PNG", headers={"content-type": "image/png"}
                )
            if url.path == "/private/secret":
                return httpx.Response(200, html="<title>secret</title>")
            k = 0 if url.path == "/" else int(url.path.removeprefix("/p"))
            links = [f"/p{i}" for i in range(1, n_pages + 1)] if k == 0 else []
            links += [
                f"/p{min(k + 1, n_pages)}",
                f"/p{k}#again" if k else "/#again",  # same page with a fragment
                "/?b=2&a=1",  # query order variants
                "/?a=1&b=2",
                "/private/secret",  # robots.txt says no
                "https://elsewhere.test/page",  # another domain
                "/broken",
                "/image.png",
            ]
            body = "".join(f'<a href="{h}">x</a>' for h in links)
            return httpx.Response(
                200, html=f"<title>Page {k}</title><article>page {k} {body}</article>"
            )
        finally:
            peak["now"] -= 1

    return httpx.MockTransport(handler), requests, peak


FAST = CrawlConfig(
    max_pages=100, concurrency=4, per_host=4, delay=0.0, retry_base=0.0, retry_jitter=0.0
)


def collect(
    config: CrawlConfig, transport: httpx.MockTransport, stats: CrawlStats | None = None
) -> list[str]:
    async def go() -> list[str]:
        return [
            p.url
            async for p in crawl(["https://site.test/"], config, stats=stats, transport=transport)
        ]

    return run(go())


def test_crawl_visits_each_page_once_and_obeys_the_rules() -> None:
    transport, requests, _ = fake_site(10)
    stats = CrawlStats()
    urls = collect(FAST, transport, stats)
    assert len(urls) == len(set(urls)) == 12  # /, /?a=1&b=2, /p1..p10
    assert max(requests.values()) <= 3  # /broken: 3 attempts; everything else exactly once
    assert requests["site.test/broken"] == 3
    assert all(n == 1 for key, n in requests.items() if key != "site.test/broken")
    assert "site.test/private/secret" not in requests  # robots.txt
    assert not any(key.startswith("elsewhere.test") for key in requests)  # allow-list
    assert stats.robots_blocked == 1 and stats.errors == 1 and stats.skipped == 1


def test_crawl_stops_exactly_at_max_pages() -> None:
    transport, requests, _ = fake_site(50)
    config = CrawlConfig(max_pages=7, concurrency=5, per_host=5, delay=0.0, retry_base=0.0)
    urls = collect(config, transport)
    assert len(urls) == 7
    pages_requested = sum(n for key, n in requests.items() if "/p" in key or key.endswith("/"))
    assert pages_requested <= 7 + 1  # no requests wasted beyond the budget (+ robots.txt)


def test_per_host_limit_caps_requests_in_flight() -> None:
    transport, _, peak = fake_site(30, latency=0.02)
    config = CrawlConfig(max_pages=30, concurrency=10, per_host=2, delay=0.0, retry_base=0.0)
    collect(config, transport)
    assert peak["max"] == 2


def test_global_limit_caps_requests_in_flight() -> None:
    transport, _, peak = fake_site(30, latency=0.02)
    config = CrawlConfig(max_pages=30, concurrency=3, per_host=10, delay=0.0, retry_base=0.0)
    collect(config, transport)
    assert peak["max"] == 3


def test_min_delay_between_requests_to_one_host() -> None:
    starts: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        starts.append(time.perf_counter())
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        links = "".join(f'<a href="/p{i}">x</a>' for i in range(1, 6))
        return httpx.Response(200, html=f"<title>t</title>{links}")

    config = CrawlConfig(max_pages=6, concurrency=5, per_host=5, delay=0.05, retry_base=0.0)
    collect(config, httpx.MockTransport(handler))
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert len(starts) == 7 and min(gaps) >= 0.04  # timer granularity on Windows is ~15 ms


def test_breaking_out_early_cleans_up() -> None:
    transport, _, _ = fake_site(40, latency=0.01)

    async def go() -> tuple[int, int]:
        got = 0
        async with aclosing(crawl(["https://site.test/"], FAST, transport=transport)) as pages:
            async for _ in pages:
                got += 1
                if got == 3:
                    break
        await asyncio.sleep(0.05)
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        return got, len(others)

    assert run(go()) == (3, 0)  # driver, workers and client are gone


def test_on_fetch_sees_every_request() -> None:
    transport, requests, _ = fake_site(3)
    logged: list[tuple[int | None, str]] = []

    async def go() -> None:
        async for _ in crawl(
            ["https://site.test/"],
            FAST,
            transport=transport,
            on_fetch=lambda r, depth: logged.append((r.status, r.url)),
        ):
            pass

    run(go())
    assert (500, "https://site.test/broken") in logged
    assert len(logged) == sum(requests.values()) - requests["site.test/robots.txt"] - 2  # retries


# --- findex crawl + iter_documents + index ----------------------------------------------


def test_cli_crawl_streams_jsonl_that_findex_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport, _, _ = fake_site(5)
    monkeypatch.setattr(findex.cli, "crawl_transport", transport)
    out, log_file, index_file = (
        tmp_path / "crawl.jsonl",
        tmp_path / "crawl.log",
        tmp_path / "c.fidx",
    )
    runner = CliRunner()
    result = runner.invoke(
        findex.cli.app,
        [
            "crawl",
            "https://site.test/",
            "--out",
            str(out),
            "--log",
            str(log_file),
            "--delay",
            "0",
            "--max-pages",
            "4",
            "--debug",
        ],
    )
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert (
        len(records) == 4 and {"url", "title", "text", "fetched_at", "status"} <= records[0].keys()
    )
    log_text = log_file.read_text(encoding="utf-8")
    assert "findex.crawler.urls: 200" in log_text and "robots.txt" in log_text
    assert "Executing" not in log_text  # asyncio debug mode: nothing blocked the loop

    docs = list(iter_documents(out))
    assert docs[0].source == records[0]["url"] and docs[0].text.startswith(records[0]["title"])
    built = runner.invoke(findex.cli.app, ["index", str(out), "--out", str(index_file)])
    assert built.exit_code == 0, built.output
    found = runner.invoke(findex.cli.app, ["search", str(index_file), "page", "--json"])
    hits = [json.loads(line) for line in found.stdout.splitlines()]
    assert hits and hits[0]["path"].startswith("https://site.test/")


def test_cli_crawl_rejects_non_http_seed() -> None:
    result = CliRunner().invoke(findex.cli.app, ["crawl", "ftp://x.test/"])
    assert result.exit_code == 1 and "not an http(s) URL" in result.stderr
