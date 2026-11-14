"""Three ranking sanity checks on the real corpus.

    uv run python -m findex.sanity index.fidx

1. A document with a rare query term outranks one with only a common term.
2. With BM25 the 20th repetition of a term adds almost nothing (TF-IDF keeps growing).
3. A short document with one match outranks a very long one with one match (BM25).
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from findex.index import Index, Posting
from findex.rank import BM25, SCORERS, Searcher, TfIdf
from findex.store import load


def check_rare_vs_common(index: Index, common: str, rare: str) -> None:
    print(f"## 1. rare vs common: `{common} OR {rare}`")
    print(f"df({common}) = {index.df(common)}, df({rare}) = {index.df(rare)} of {index.num_docs}\n")
    has_rare = set(index.doc_ids(rare))
    searcher = Searcher(index)
    for name, scorer in SCORERS.items():
        results = searcher.search(f"{common} OR {rare}", scorer, k=len(has_rare) + 3)
        print(f"{name}:")
        print("| rank | score | doc | has rare term |")
        print("|---|---|---|---|")
        for rank, r in enumerate(results, start=1):
            mark = "yes" if r.doc_id in has_rare else "no"
            print(f"| {rank} | {r.score:.3f} | [{r.doc_id}] {r.title[:45]} | {mark} |")
        ranks_with = [i for i, r in enumerate(results) if r.doc_id in has_rare]
        ok = max(ranks_with) < min(i for i, r in enumerate(results) if r.doc_id not in has_rare)
        print(f"\nall {len(has_rare)} docs with `{rare}` rank above the rest: {ok}\n")


def check_saturation(index: Index, term: str) -> None:
    doc_id = min(index.doc_lengths, key=lambda d: abs(index.doc_length(d) - index.avg_doc_length))
    print(f"## 2. tf saturation for `{term}` (df={index.df(term)})")
    print(f"doc [{doc_id}] of average length ({index.doc_length(doc_id)} tokens)\n")
    bm25, tfidf = BM25(), TfIdf()
    print("| tf | BM25 | gain of this occurrence | TF-IDF | gain of this occurrence |")
    print("|---|---|---|---|---|")
    prev_b = prev_t = 0.0
    for tf in range(1, 21):
        b = bm25(term, Posting(doc_id, tf), index)
        t = tfidf(term, Posting(doc_id, tf), index)
        if tf in (1, 2, 3, 5, 10, 19, 20):
            print(f"| {tf} | {b:.3f} | +{b - prev_b:.3f} | {t:.3f} | +{t - prev_t:.3f} |")
        prev_b, prev_t = b, t
    idf = math.log(1 + (index.num_docs - index.df(term) + 0.5) / (index.df(term) + 0.5))
    limit = idf * (bm25.k1 + 1)
    print(f"\nBM25 upper bound idf·(k1+1) = {limit:.3f}; at tf=20 it is at {prev_b / limit:.0%}\n")


def pick_short_long(index: Index) -> tuple[str, int, int]:
    """A word with tf=1 in both a very short and a very long document (max length ratio)."""
    best = ("", 0, 0, 0.0)
    for term, plist in index.items():
        if not term.isalpha() or len(term) < 5 or len(plist) < 2:
            continue
        ones = [p.doc_id for p in plist if p.tf == 1]
        if len(ones) < 2:
            continue
        short = min(ones, key=index.doc_length)
        long = max(ones, key=index.doc_length)
        ratio = index.doc_length(long) / index.doc_length(short)
        if ratio > best[3]:
            best = (term, short, long, ratio)
    return best[0], best[1], best[2]


def check_short_vs_long(index: Index) -> None:
    term, short, long = pick_short_long(index)
    print(f"## 3. short vs long, one occurrence each: `{term}` (df={index.df(term)})\n")
    print("| doc | length, tokens | tf | BM25 | TF-IDF |")
    print("|---|---|---|---|---|")
    for doc_id in (short, long):
        posting = next(p for p in index[term] if p.doc_id == doc_id)
        title = index.doc_meta[doc_id].title[:40]
        print(
            f"| [{doc_id}] {title} | {index.doc_length(doc_id):,} | {posting.tf} "
            f"| {BM25()(term, posting, index):.3f} | {TfIdf()(term, posting, index):.3f} |"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.sanity", description=__doc__)
    parser.add_argument("index", type=Path)
    parser.add_argument("--common", default="river")
    parser.add_argument("--rare", default="locomotive")
    args = parser.parse_args(argv)
    index = load(args.index)
    check_rare_vs_common(index, args.common, args.rare)
    check_saturation(index, args.rare)
    check_short_vs_long(index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
