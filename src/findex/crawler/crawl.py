"""`crawl()` — an async generator of pages from a polite, concurrent crawl.

    async for page in crawl(["https://books.toscrape.com/"], CrawlConfig(max_pages=200)):
        ...

Structure:

    frontier (asyncio.Queue of (url, depth))
        └── N worker tasks in a TaskGroup ── robots? ── per-host semaphore + delay
                                                     └── global semaphore ── fetch()
        └── results (asyncio.Queue of Page) ──> the generator yields them

* ``concurrency`` workers and one global semaphore of the same size cap all
  requests in flight (robots.txt included).
* A semaphore per host (``per_host``) plus a minimum interval between request
  *starts* to the same host (``delay``, raised to robots.txt Crawl-delay) keep
  the crawl polite even with 20 workers.
* URLs are canonicalised (`findex.crawler.urls`) and recorded in ``seen`` when
  they are queued, so no URL is fetched twice.
* ``max_pages`` is a budget: a worker reserves a slot before fetching and gives
  it back if the page fails or is not HTML, so the crawl stops after exactly
  ``max_pages`` pages without wasting requests. Then the workers are cancelled
  and the HTTP client is closed.

Stopping early: ``break`` out of ``async for`` does *not* close an async
generator right away (its ``finally`` would run later, whenever it is garbage
collected). Wrap it so cleanup is deterministic::

    async with contextlib.aclosing(crawl(seeds, config)) as pages:
        async for page in pages:
            ...

The TaskGroup runs in its own *driver* task, not around the ``yield``: a
``yield`` inside a TaskGroup would suspend the group in the consumer's task,
and cancellation meant for the workers could land in the consumer (PEP 789).
The generator only reads finished pages from a queue, and its ``finally``
cancels the driver — so breaking out of ``async for`` cleans everything up.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from findex.crawler.fetch import USER_AGENT, FetchResult, fetch
from findex.crawler.robots import RobotsCache
from findex.crawler.urls import canonicalize, host_of, parse_html

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CrawlConfig:
    max_pages: int = 500
    concurrency: int = 10
    per_host: int = 4
    delay: float = 0.1  # min seconds between request starts to one host
    timeout: float = 10.0
    max_depth: int | None = None
    allowed_domains: frozenset[str] | None = None  # None: the hosts of the seeds
    user_agent: str = USER_AGENT
    retry_base: float = 0.5  # backoff base and jitter for fetch() retries
    retry_jitter: float = 1.0


@dataclass(frozen=True, slots=True)
class Page:
    url: str
    title: str
    text: str
    fetched_at: str  # ISO 8601, UTC
    status: int
    depth: int
    elapsed: float


@dataclass(slots=True)
class CrawlStats:
    """Live counters (read by the progress display)."""

    started: float = field(default_factory=time.perf_counter)
    pages: int = 0
    errors: int = 0
    in_flight: int = 0
    queued: int = 0
    robots_blocked: int = 0
    skipped: int = 0  # fetched fine but not HTML
    requests: int = 0

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    @property
    def pages_per_second(self) -> float:
        return self.pages / self.elapsed if self.elapsed > 0 else 0.0


type FetchLogger = Callable[[FetchResult, int], None]


def make_client(
    config: CrawlConfig, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """One shared client per crawl: one connection pool, keep-alive across requests."""
    return httpx.AsyncClient(
        headers={"User-Agent": config.user_agent},
        follow_redirects=True,
        max_redirects=5,
        timeout=config.timeout + 5,  # backstop; fetch() enforces asyncio.timeout itself
        limits=httpx.Limits(
            max_connections=config.concurrency, max_keepalive_connections=config.concurrency
        ),
        transport=transport,
    )


async def crawl(
    seeds: Iterable[str],
    config: CrawlConfig | None = None,
    *,
    stats: CrawlStats | None = None,
    on_fetch: FetchLogger | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncGenerator[Page]:
    """Yield pages as they are fetched (see module docstring)."""
    config = config or CrawlConfig()
    stats = stats if stats is not None else CrawlStats()
    results: asyncio.Queue[Page] = asyncio.Queue()
    driver = asyncio.create_task(_drive(list(seeds), config, stats, results, on_fetch, transport))
    getter: asyncio.Task[Page] | None = None
    try:
        while True:
            getter = asyncio.create_task(results.get())
            done, _ = await asyncio.wait({getter, driver}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield getter.result()
                continue
            getter.cancel()
            while not results.empty():  # pages that arrived just before the driver ended
                yield results.get_nowait()
            driver.result()  # re-raise if the crawl itself failed
            return
    finally:
        for task in (getter, driver):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def _drive(
    seeds: list[str],
    config: CrawlConfig,
    stats: CrawlStats,
    results: asyncio.Queue[Page],
    on_fetch: FetchLogger | None,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    frontier: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
    seen: set[str] = set()
    for seed in seeds:
        canonical = canonicalize(seed)
        if canonical and canonical not in seen:
            seen.add(canonical)
            frontier.put_nowait((canonical, 0))
    allowed = config.allowed_domains or frozenset(host_of(u) for u in seen)

    global_slots = asyncio.Semaphore(config.concurrency)
    host_slots: defaultdict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(config.per_host)
    )
    host_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    host_next_start: dict[str, float] = {}
    reserved = 0
    done = asyncio.Event()

    async with make_client(config, transport) as client:

        async def limited_fetch(url: str, delay: float) -> FetchResult:
            host = host_of(url)
            async with host_slots[host]:
                async with host_locks[host]:  # schedule this host's next start time
                    loop_now = asyncio.get_running_loop().time()
                    start_at = max(loop_now, host_next_start.get(host, loop_now))
                    host_next_start[host] = start_at + delay
                if start_at > loop_now:
                    await asyncio.sleep(start_at - loop_now)
                async with global_slots:
                    stats.in_flight += 1
                    stats.requests += 1
                    try:
                        return await fetch(
                            client,
                            url,
                            timeout=config.timeout,
                            base_delay=config.retry_base,
                            jitter=config.retry_jitter,
                        )
                    finally:
                        stats.in_flight -= 1

        robots = RobotsCache(
            client,
            user_agent=config.user_agent,
            fetcher=lambda url: limited_fetch(url, config.delay),
        )

        async def process(url: str, depth: int) -> None:
            nonlocal reserved
            if done.is_set():
                return
            if not await robots.allowed(url):
                stats.robots_blocked += 1
                log.info("robots.txt disallows %s", url)
                return
            if reserved >= config.max_pages:
                return
            reserved += 1  # claim a page slot before spending a request on it
            crawl_delay = await robots.crawl_delay(url)
            result = await limited_fetch(url, max(config.delay, crawl_delay or 0.0))
            if on_fetch:
                on_fetch(result, depth)
            if not result.is_html:
                reserved -= 1
                if result.ok:
                    stats.skipped += 1
                else:
                    stats.errors += 1
                return
            final = canonicalize(result.final_url) or url
            seen.add(final)  # a redirect target counts as seen too
            parsed = parse_html(result.text(), final)
            if config.max_depth is None or depth < config.max_depth:
                for link in parsed.links:
                    if link not in seen and host_of(link) in allowed:
                        seen.add(link)
                        frontier.put_nowait((link, depth + 1))
                stats.queued = frontier.qsize()
            page = Page(
                final,
                parsed.title,
                parsed.text,
                datetime.now(UTC).isoformat(timespec="seconds"),
                result.status or 0,
                depth,
                result.elapsed,
            )
            await results.put(page)
            stats.pages += 1
            if stats.pages >= config.max_pages:
                done.set()

        async def worker() -> None:
            while True:
                url, depth = await frontier.get()
                stats.queued = frontier.qsize()
                try:
                    await process(url, depth)
                finally:
                    frontier.task_done()

        async def supervisor(workers: list[asyncio.Task[None]]) -> None:
            finished = asyncio.create_task(frontier.join())  # nothing left to crawl
            budget = asyncio.create_task(done.wait())  # max_pages reached
            try:
                await asyncio.wait({finished, budget}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                # Also when the supervisor itself is cancelled: asyncio.wait() does not
                # cancel the tasks it waits for, so without this they would leak.
                for task in (finished, budget, *workers):
                    task.cancel()
            stats.queued = frontier.qsize()

        async with asyncio.TaskGroup() as group:
            workers = [group.create_task(worker()) for _ in range(config.concurrency)]
            group.create_task(supervisor(workers))
    log.info(
        "crawl finished: %d pages, %d errors, %d requests in %.1f s",
        stats.pages,
        stats.errors,
        stats.requests,
        stats.elapsed,
    )
