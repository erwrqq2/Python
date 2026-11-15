"""robots.txt, fetched once per host and cached.

What a missing or broken robots.txt means (as Google documents it):

* 200            -> parse it and obey it;
* 404 / other 4xx -> no rules: everything is allowed;
* 5xx / timeout / connection error -> assume everything is disallowed for this
  host (the server is struggling; better not to crawl it at all).

A per-host ``asyncio.Lock`` makes 20 workers that hit a new host at the same
moment wait for one download instead of fetching robots.txt 20 times.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from findex.crawler.fetch import USER_AGENT, FetchResult, fetch

log = logging.getLogger(__name__)

type Fetcher = Callable[[str], Awaitable[FetchResult]]


class RobotsCache:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str = USER_AGENT,
        timeout: float = 10.0,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.user_agent = user_agent
        self._fetch: Fetcher = fetcher or (lambda url: fetch(client, url, timeout=timeout))
        self._parsers: dict[str, RobotFileParser] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.fetches = 0  # how many robots.txt downloads happened (tests check the cache)

    async def allowed(self, url: str) -> bool:
        parser = await self._parser_for(url)
        return parser.can_fetch(self.user_agent, url)

    async def crawl_delay(self, url: str) -> float | None:
        parser = await self._parser_for(url)
        delay = parser.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None

    async def _parser_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._parsers:
            return self._parsers[origin]
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._parsers:  # another worker may have filled it meanwhile
                self._parsers[origin] = await self._download(origin)
        return self._parsers[origin]

    async def _download(self, origin: str) -> RobotFileParser:
        robots_url = f"{origin}/robots.txt"
        self.fetches += 1
        result = await self._fetch(robots_url)
        parser = RobotFileParser(robots_url)
        if result.ok:
            parser.parse(result.text().splitlines())
            log.info("robots.txt %s: %d bytes, obeying it", robots_url, len(result.body))
        elif result.status is not None and 400 <= result.status < 500:
            parser.parse([])  # no rules -> allow all
            log.info("robots.txt %s: HTTP %d, no restrictions", robots_url, result.status)
        else:
            parser.parse(["User-agent: *", "Disallow: /"])
            log.warning("robots.txt %s: %s, not crawling this host", robots_url, result.error)
        return parser
