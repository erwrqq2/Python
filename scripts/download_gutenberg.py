"""Download a reproducible sample of English Project Gutenberg books into data/.

Usage:
    uv run python scripts/download_gutenberg.py --count 150

What it does:
  1. Downloads the official catalog (pg_catalog.csv) from the pglaf.org mirror
     (Project Gutenberg asks robots to use mirrors, not www.gutenberg.org).
  2. Picks `--count` English "Text" books with a fixed random seed, so everyone
     running the script gets the same corpus.
  3. Downloads each plain-text book, strips the Gutenberg license header/footer
     and saves it as data/gutenberg/<id>.txt (first line = title).

The script is resumable: already downloaded books are skipped.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MIRROR = "https://gutenberg.pglaf.org"
CATALOG_URL = f"{MIRROR}/cache/epub/feeds/pg_catalog.csv"
BOOK_URL = MIRROR + "/cache/epub/{id}/pg{id}.txt"
USER_AGENT = "findex-course-lab/0.1"

START_MARK = "*** START OF"
END_MARK = "*** END OF"
MAX_FAILURES_IN_A_ROW = 5


def fetch(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def load_catalog(path: Path) -> list[tuple[int, str]]:
    """Return (id, title) for every English text in the catalog."""
    if not path.exists():
        print(f"downloading catalog -> {path}")
        path.write_bytes(fetch(CATALOG_URL, timeout=300))
    books = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["Type"] == "Text" and row["Language"] == "en":
                title = " ".join(row["Title"].split())
                books.append((int(row["Text#"]), title))
    return books


def strip_boilerplate(text: str) -> str:
    """Keep only the book body between the START/END license markers."""
    start = text.find(START_MARK)
    start = text.find("\n", start) + 1 if start != -1 else 0
    end = text.find(END_MARK, start)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = parser.parse_args(argv)

    books_dir = args.out / "gutenberg"
    books_dir.mkdir(parents=True, exist_ok=True)

    catalog = load_catalog(args.out / "pg_catalog.csv")
    rng = random.Random(args.seed)
    rng.shuffle(catalog)

    done = failures = 0
    for book_id, title in catalog:
        if done >= args.count:
            break
        target = books_dir / f"{book_id}.txt"
        if target.exists():
            done += 1
            continue
        try:
            raw = fetch(BOOK_URL.format(id=book_id))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"skip {book_id}: {exc}", file=sys.stderr)
            failures += 1
            if failures >= MAX_FAILURES_IN_A_ROW:
                print("network looks down, stopping (rerun to resume)", file=sys.stderr)
                break
            continue
        finally:
            time.sleep(args.delay)
        failures = 0
        body = strip_boilerplate(raw.decode("utf-8-sig", errors="replace"))
        if len(body) < 2_000:  # skip near-empty entries (indexes, audio stubs)
            continue
        target.write_text(f"{title}\n\n{body}\n", encoding="utf-8")
        done += 1
        print(f"[{done}/{args.count}] {book_id} {title[:60]}")

    total = sum(p.stat().st_size for p in books_dir.glob("*.txt"))
    print(f"{done} books, {total / 2**20:.1f} MiB in {books_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
