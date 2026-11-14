"""Boolean search over a saved index.

    uv run python -m findex.search index.bin "whale ship"
    uv run python -m findex.search index.bin "whale OR shark NOT ship" --engine set

Query language (evaluated strictly left to right, no precedence, no brackets —
a real parser comes in Lab 3):

    term term        AND (implicit)
    a AND b          AND
    a OR b           OR
    a NOT b          a AND NOT b  (documents with a but without b)
    NOT a            every document without a
    a OR NOT b       a, or anything without b

Words are run through the same tokenizer as the documents, so "Whale" finds
"whale"; a word that splits into several tokens ("well-known") means all of
them (AND).

Two engines with the same interface:

* ``merge`` — two-pointer walk over the sorted doc_id lists. O(len(a)+len(b)),
  no hashing, no extra memory beyond the output.
* ``set``   — ``set(a) & set(b)`` etc. Also O(len(a)+len(b)) on average, but
  every id is hashed into a new set first; the loops run in C.
"""

from __future__ import annotations

import argparse
import time
import tracemalloc
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from findex.index import Index
from findex.tokenize import tokenize

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


# --- query parsing and evaluation ---------------------------------------------------


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class Clause:
    op: str  # "AND" or "OR": how this clause joins the result so far
    negated: bool
    terms: tuple[str, ...]  # tokens of one query word, ANDed together


def parse(query: str) -> list[Clause]:
    """Split a query into left-to-right clauses. Operators must be UPPERCASE."""
    clauses: list[Clause] = []
    op, negated, pending_op = "AND", False, False
    for word in query.split():
        if word in ("AND", "OR"):
            if not clauses or pending_op:
                raise QueryError(f"{word!r} needs a term on both sides")
            op, pending_op = word, True
            continue
        if word == "NOT":
            negated = not negated
            pending_op = True
            continue
        terms = tuple(tokenize(word))
        if not terms:
            continue  # pure punctuation, ignore
        clauses.append(Clause(op, negated, terms))
        op, negated, pending_op = "AND", False, False
    if pending_op:
        raise QueryError("query ends with an operator")
    if not clauses:
        raise QueryError("query has no searchable terms")
    return clauses


def evaluate(query: str, index: Index, engine: Engine = MERGE) -> list[int]:
    """Return sorted doc_ids matching `query`."""
    result: Collection[int] | None = None
    everything = None
    for clause in parse(query):
        operand = engine.make(index.doc_ids(clause.terms[0]))
        for term in clause.terms[1:]:
            operand = engine.and_(operand, engine.make(index.doc_ids(term)))

        if clause.negated and (result is None or clause.op == "OR"):
            if everything is None:
                everything = engine.make(index.all_doc_ids())
            operand = engine.not_(everything, operand)  # complement
        if result is None:
            result = operand
        elif clause.op == "OR":
            result = engine.or_(result, operand)
        elif clause.negated:
            result = engine.not_(result, operand)  # a NOT b, no complement needed
        else:
            result = engine.and_(result, operand)
    return engine.finish(result)


# --- CLI ----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from findex.store import load

    parser = argparse.ArgumentParser(prog="python -m findex.search", description="Boolean search.")
    parser.add_argument("index", type=Path, help="index file built by findex.index")
    parser.add_argument("query")
    parser.add_argument("--engine", choices=ENGINES, default="merge")
    parser.add_argument("--show", type=int, default=10, help="how many hits to print")
    args = parser.parse_args(argv)

    tracemalloc.start()
    t0 = time.perf_counter()
    index = load(args.index)
    t1 = time.perf_counter()
    try:
        hits = evaluate(args.query, index, ENGINES[args.engine])
    except QueryError as exc:
        parser.error(str(exc))
    t2 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"query:       {args.query!r} (engine={args.engine})")
    print(f"{len(hits)} of {index.num_docs} documents match")
    for doc_id in hits[: args.show]:
        meta = index.doc_meta[doc_id]
        print(f"  [{doc_id:>4}] {meta.title[:70]}  ({meta.path})")
    if len(hits) > args.show:
        print(f"  ... and {len(hits) - args.show} more")
    print(f"load:        {t1 - t0:.3f} s")
    print(f"search:      {(t2 - t1) * 1000:.3f} ms")
    print(f"peak memory: {peak / 2**20:.1f} MiB (tracemalloc)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
