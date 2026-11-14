"""Benchmark: merge vs set engine.

    uv run python -m findex.bench index.bin

Queries on the two most frequent terms (longest posting lists), the two rarest
(df = 1) and one frequent + one rare term, for AND / OR / NOT. Each cell is the
best of 5 repeats of `timeit` (number chosen automatically), in microseconds (us).

The posting lists of a 143-document corpus are short (at most 143 ids), so a
second, synthetic part repeats the bare operations on lists of 10^3..10^6 ids.
"""

from __future__ import annotations

import argparse
import random
import timeit
from pathlib import Path

from findex.index import Index
from findex.search import ENGINES, MERGE, SET, evaluate
from findex.store import load


def best_us(func, repeat: int = 5) -> float:
    timer = timeit.Timer(func)
    number, _ = timer.autorange()
    return min(timer.repeat(repeat=repeat, number=number)) / number * 1e6


def pick_terms(index: Index) -> tuple[list[str], list[str]]:
    """Two terms with the longest posting lists (ties: most occurrences) and two
    df=1 terms (a fixed random pick among ordinary words, so it is reproducible)."""

    def cf(term: str) -> int:
        return sum(p.tf for p in index.postings[term])

    frequent = sorted(index.postings, key=lambda t: (-index.df(t), -cf(t)))[:2]
    singles = sorted(t for t in index.postings if index.df(t) == 1 and t.isalpha() and len(t) > 5)
    rare = random.Random(0).sample(singles, 2)
    return frequent, rare


def corpus_benchmark(index: Index) -> None:
    frequent, rare = pick_terms(index)
    pairs = {
        "2 most frequent": frequent,
        "2 rarest": rare,
        "frequent + rare": [frequent[0], rare[0]],
    }
    print("| Terms (df) | Query | merge, us | set, us | Hits |")
    print("|---|---|---|---|---|")
    for label, (a, b) in pairs.items():
        for template in ("{a} {b}", "{a} OR {b}", "{a} NOT {b}"):
            query = template.format(a=a, b=b)
            hits = evaluate(query, index, MERGE)
            assert hits == evaluate(query, index, SET)
            times = {
                name: best_us(lambda e=engine, q=query: evaluate(q, index, e))
                for name, engine in ENGINES.items()
            }
            print(
                f"| {label}: {a} ({index.df(a)}), {b} ({index.df(b)}) | `{query}` "
                f"| {times['merge']:.1f} | {times['set']:.1f} | {len(hits)} |"
            )


def synthetic_benchmark() -> None:
    rng = random.Random(0)
    print("| List sizes | AND merge, us | AND set, us | set / merge |")
    print("|---|---|---|---|")
    for n in (10**3, 10**4, 10**5, 10**6):
        universe = range(4 * n)
        a = sorted(rng.sample(universe, n))
        b = sorted(rng.sample(universe, n))
        merge = best_us(lambda a=a, b=b: MERGE.and_(a, b), repeat=3)
        set_ = best_us(lambda a=a, b=b: sorted(set(a) & set(b)), repeat=3)
        print(f"| {n:,} & {n:,} | {merge:,.0f} | {set_:,.0f} | {set_ / merge:.2f} |")
    for n, m in ((10**6, 10), (10**6, 1000)):
        universe = range(4 * n)
        a = sorted(rng.sample(universe, n))
        b = sorted(rng.sample(universe, m))
        merge = best_us(lambda a=a, b=b: MERGE.and_(a, b), repeat=3)
        set_ = best_us(lambda a=a, b=b: sorted(set(a) & set(b)), repeat=3)
        print(f"| {n:,} & {m:,} | {merge:,.0f} | {set_:,.0f} | {set_ / merge:.2f} |")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.bench", description=__doc__)
    parser.add_argument("index", type=Path)
    parser.add_argument("--no-synthetic", action="store_true")
    args = parser.parse_args(argv)
    corpus_benchmark(load(args.index))
    if not args.no_synthetic:
        print()
        synthetic_benchmark()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
