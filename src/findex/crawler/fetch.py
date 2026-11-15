"""One polite HTTP GET with a timeout, bounded retries, backoff + jitter and Retry-After.

Retried (at most `max_attempts` = 3 attempts in total):
    429 Too Many Requests, any 5xx, a timeout, a dropped/refused connection.
Not retried:
    every other 4xx (404 stays 404), too many redirects (a redirect loop does
    not fix itself), a malformed URL.

Delay before attempt n+1 (n = 1, 2):
    ``min(max_delay, base_delay * 2**(n-1)) + uniform(0, jitter)``;
    if the server sent ``Retry-After`` (seconds or an HTTP date), wait at least
    that long (capped at `max_retry_after`). Jitter spreads the retries of many
    workers so they do not hit the server again in lock-step.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

import httpx

log = logging.getLogger(__name__)

USER_AGENT: Final = "findex-crawler/0.6 (+https://github.com/erwrqq2/Python)"


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str  # what was asked for
    final_url: str  # after redirects
    status: int | None  # None: no HTTP response (timeout, connection error, redirect loop)
    content_type: str
    body: bytes
    encoding: str
    elapsed: float  # seconds, all attempts and waits included
    attempts: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def is_html(self) -> bool:
        return self.ok and "html" in self.content_type

    def text(self) -> str:
        return self.body.decode(self.encoding or "utf-8", errors="replace")


def is_retryable_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a Retry-After header: ``"120"`` or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def backoff_delay(
    attempt: int, *, base: float, cap: float, jitter: float, rng: Callable[[], float]
) -> float:
    """Exponential backoff with additive jitter for the wait after `attempt` (1-based)."""
    return min(cap, base * 2 ** (attempt - 1)) + rng() * jitter


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 10.0,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: float = 1.0,
    max_retry_after: float = 60.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
) -> FetchResult:
    """GET `url` with retries; never raises for network/HTTP errors (they are in the result).

    ``asyncio.CancelledError`` is *not* caught: cancelling a crawl must stop it.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    start = time.perf_counter()
    attempt = 0
    status: int | None = None
    error: str | None = None
    retry_after: float | None = None
    for attempt in range(1, max_attempts + 1):
        retryable = False
        try:
            async with asyncio.timeout(timeout):
                response = await client.get(url)
        except TimeoutError:
            status, error, retryable = None, f"timeout after {timeout:g}s", True
        except httpx.TooManyRedirects:
            status, error = None, "too many redirects (loop?)"
        except httpx.TransportError as exc:  # connect/read errors, protocol errors
            status, error, retryable = None, f"{type(exc).__name__}: {exc}", True
        except httpx.InvalidURL as exc:
            status, error = None, f"invalid URL: {exc}"
        else:
            status, error = response.status_code, None
            retryable = is_retryable_status(status)
            if not retryable or attempt == max_attempts:
                return FetchResult(
                    url,
                    str(response.url),
                    status,
                    response.headers.get("content-type", ""),
                    response.content,
                    response.encoding or "utf-8",
                    time.perf_counter() - start,
                    attempt,
                    None if response.is_success else f"HTTP {status}",
                )
            retry_after = parse_retry_after(response.headers.get("retry-after"))
        if not retryable or attempt == max_attempts:
            break
        delay = backoff_delay(attempt, base=base_delay, cap=max_delay, jitter=jitter, rng=rng)
        if retry_after is not None:
            delay = max(delay, min(retry_after, max_retry_after))
        reason = f"HTTP {status}" if status is not None else error
        log.warning(
            "retry %s in %.2fs (attempt %d/%d failed: %s)",
            url,
            delay,
            attempt,
            max_attempts,
            reason,
        )
        await sleep(delay)
        retry_after = None
    return FetchResult(
        url, url, status, "", b"", "utf-8", time.perf_counter() - start, attempt, error
    )
