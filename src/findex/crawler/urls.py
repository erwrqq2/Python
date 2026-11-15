"""URL canonicalisation and HTML parsing (links, title, text).

Canonical form — one string per page, so the `seen` set catches duplicates:

* only http/https; scheme and host lower-case; default ports dropped
  (``HTTP://Example.COM:80/a`` -> ``http://example.com/a``);
* the ``#fragment`` is dropped (same document);
* an empty path becomes ``/``; a trailing slash on any other path is removed
  (``/docs/`` and ``/docs`` are treated as one page; if the server insists on
  one form it redirects, and the redirect target is canonicalised too);
* query parameters are sorted (``?b=2&a=1`` -> ``?a=1&b=2``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

_DEFAULT_PORTS: Final = {"http": 80, "https": 443}
_NOISE_TAGS: Final = ["script", "style", "noscript", "template", "nav", "header", "footer", "form"]


def canonicalize(url: str, base: str | None = None) -> str | None:
    """Canonical absolute URL, or None if it is not an http(s) URL."""
    try:
        parts = urlsplit(urljoin(base, url) if base else url)
        port = parts.port
    except ValueError:  # malformed, e.g. "http://[::1" or a bad port
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    # Resolve "." and ".." segments (urljoin does it per RFC 3986): /a/./b/../c -> /a/c
    path = urlsplit(urljoin("http://x/", parts.path or "/")).path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    return urlsplit(url).netloc


@dataclass(frozen=True, slots=True)
class ParsedPage:
    title: str
    text: str
    links: list[str]  # absolute, canonical, deduplicated, in document order


def parse_html(html: str, base_url: str) -> ParsedPage:
    """Title, visible main text and outgoing links of one HTML page."""
    tree = LexborHTMLParser(html)
    title_node = tree.css_first("title")
    title = " ".join(title_node.text(strip=True).split()) if title_node else ""

    links: dict[str, None] = {}  # ordered set
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href")
        if href and not href.startswith(("mailto:", "javascript:", "tel:")):
            canonical = canonicalize(href, base_url)
            if canonical:
                links[canonical] = None

    tree.strip_tags(_NOISE_TAGS, recursive=True)  # menus and scripts are not content
    main = tree.css_first("article") or tree.css_first("main") or tree.body
    text = " ".join(main.text(separator=" ").split()) if main else ""
    return ParsedPage(title or base_url, text, list(links))
