"""Polite asynchronous crawler (Lab 6): fetch, robots.txt, URL canonicalisation, crawl."""

from findex.crawler.crawl import CrawlConfig, CrawlStats, Page, crawl
from findex.crawler.fetch import USER_AGENT, FetchResult, fetch
from findex.crawler.robots import RobotsCache
from findex.crawler.urls import canonicalize

__all__ = [
    "USER_AGENT",
    "CrawlConfig",
    "CrawlStats",
    "FetchResult",
    "Page",
    "RobotsCache",
    "canonicalize",
    "crawl",
    "fetch",
]
