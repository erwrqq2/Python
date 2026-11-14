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
from typing import Final, Literal

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

type EngineName = Literal["merge", "set"]
ENGINE_NAMES: Final[tuple[EngineName, ...]] = ("merge", "set")


@dataclass(frozen=True)
class Engine[C]:
    """How operands are represented (`C`) and combined. `finish` returns sorted doc_ids.

    Generic in the collection type, so the type checker knows MERGE works on
    sequences and SET on sets — no ``Any`` in between.
    """

    name: EngineName
    make: Callable[[Sequence[int]], C]
    and_: Callable[[C, C], C]
    or_: Callable[[C, C], C]
    not_: Callable[[C, C], C]  # a AND NOT b
    finish: Callable[[C], list[int]]


def _as_is(ids: Sequence[int]) -> Sequence[int]:
    return ids


def _set_and(a: set[int], b: set[int]) -> set[int]:
    return a & b


def _set_or(a: set[int], b: set[int]) -> set[int]:
    return a | b


def _set_not(a: set[int], b: set[int]) -> set[int]:
    return a - b


MERGE: Final = Engine[Sequence[int]]("merge", _as_is, intersect, union, difference, list)
SET: Final = Engine[set[int]]("set", set, _set_and, _set_or, _set_not, sorted)
