"""Snippets: a ±80-character window around the best match, terms highlighted.

"Best" = the occurrence whose window contains the most *distinct* query terms
(ties: the earliest). Highlighting wraps matches in ``**…**``.

Matching uses one compiled regex of the query terms with word boundaries and
IGNORECASE, so it runs in C over the raw text — no need to re-tokenize a 4 MB
book in Python just to find ten words.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection

WIDTH = 80
_APOSTROPHE = "['\u2019\u2018\u02bc`]"
_WS_RE = re.compile(r"\s+")


def terms_regex(terms: Collection[str]) -> re.Pattern[str]:
    # Longest first, so "whales" wins over "whale" at the same position.
    alternatives = [
        re.escape(t).replace("'", _APOSTROPHE) for t in sorted(set(terms), key=len, reverse=True)
    ]
    return re.compile(rf"(?<![^\W_])(?:{'|'.join(alternatives)})(?![^\W_])", re.IGNORECASE)


def make_snippet(text: str, terms: Collection[str], width: int = WIDTH) -> str:
    """Return the best ±`width` window of `text` with `terms` highlighted ("" if none)."""
    if not terms:
        return ""
    hits = [(m.start(), m.end(), m.group().casefold()) for m in terms_regex(terms).finditer(text)]
    if not hits:
        return ""

    # Sliding window over hits (both pointers only move forward): for each hit as
    # the centre, count distinct terms among hits inside [start - width, end + width].
    best, best_distinct = 0, 0
    left = right = 0
    inside: Counter[str] = Counter()
    for i, (start, end, _) in enumerate(hits):
        while right < len(hits) and hits[right][1] <= end + width:
            inside[hits[right][2]] += 1
            right += 1
        while hits[left][0] < start - width:
            inside[hits[left][2]] -= 1
            if not inside[hits[left][2]]:
                del inside[hits[left][2]]
            left += 1
        if len(inside) > best_distinct:
            best, best_distinct = i, len(inside)

    start, end, _ = hits[best]
    lo, hi = max(0, start - width), min(len(text), end + width)
    pieces: list[str] = []
    cursor = lo
    for h_start, h_end, _ in hits:
        if h_start >= lo and h_end <= hi:
            pieces += [text[cursor:h_start], "**", text[h_start:h_end], "**"]
            cursor = h_end
    pieces.append(text[cursor:hi])
    body = _WS_RE.sub(" ", "".join(pieces)).strip()
    return ("…" if lo > 0 else "") + body + ("…" if hi < len(text) else "")
