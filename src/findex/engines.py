"""Set operations on sorted doc_id lists: two engines with one interface.

* ``MERGE`` — two-pointer walk over sorted lists. O(len(a)+len(b)), no hashing,
  no extra memory beyond the output.
* ``SET``   — Python sets: ``a & b``, ``a | b``, ``a - b``. Also O(len(a)+len(b))
  on average, but every id is hashed into a new set first; the loops run in C.

Lab 2 compared them (see README); since Lab 3 the query tree (`findex.query`)
is evaluated with either engine.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

# --- two-pointer merge over sorted lists -------------------------------------------


def intersect(a: Sequence[int], b: Sequence[int]) -> list[int]:
    """Ids present in both sorted sequences."""
    out: list[int] = []
    i = j = 0
    len_a, len_b = len(a), len(b)
    while i < len_a and j < len_b:
        x, y = a[i], b[j]
        if x == y:
            out.append(x)
            i += 1
            j += 1
        elif x < y:
            i += 1
        else:
            j += 1
    return out


def union(a: Sequence[int], b: Sequence[int]) -> list[int]:
    """Ids present in either sorted sequence, sorted, without duplicates."""
    out: list[int] = []
    i = j = 0
    len_a, len_b = len(a), len(b)
    while i < len_a and j < len_b:
        x, y = a[i], b[j]
        if x == y:
            out.append(x)
            i += 1
            j += 1
        elif x < y:
            out.append(x)
            i += 1
        else:
            out.append(y)
            j += 1
    out.extend(a[i:])
    out.extend(b[j:])
    return out


def difference(a: Sequence[int], b: Sequence[int]) -> list[int]:
    """Ids of sorted `a` that are not in sorted `b`."""
    out: list[int] = []
    i = j = 0
    len_a, len_b = len(a), len(b)
    while i < len_a:
        if j >= len_b:
            out.extend(a[i:])
            break
        x, y = a[i], b[j]
        if x == y:
            i += 1
            j += 1
        elif x < y:
            out.append(x)
            i += 1
        else:
            j += 1
    return out


# --- engines ------------------------------------------------------------------------


@dataclass(frozen=True)
class Engine:
    """How operands are represented and combined. `finish` returns sorted doc_ids."""

    name: str
    make: Callable[[Sequence[int]], Any]
    and_: Callable[[Any, Any], Any]
    or_: Callable[[Any, Any], Any]
    not_: Callable[[Any, Any], Any]  # a AND NOT b
    finish: Callable[[Any], list[int]]


def _as_is(ids: Sequence[int]) -> Sequence[int]:
    return ids


MERGE = Engine("merge", _as_is, intersect, union, difference, list)
SET = Engine("set", set, set.__and__, set.__or__, set.__sub__, sorted)
ENGINES = {e.name: e for e in (MERGE, SET)}
