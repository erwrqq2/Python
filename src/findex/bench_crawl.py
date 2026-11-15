"""Crawl benchmark (Lab 6): same seed, same max_pages, different concurrency.

    python -m findex.bench_crawl sweep https://books.toscrape.com/ --max-pages 200
    python -m findex.bench_crawl run https://books.toscrape.com/ --concurrency 5 --per-host 5

Each configuration runs in a fresh subprocess (peak RSS is per process). Pages
go to a temporary JSON-lines file exactly as in `findex crawl`. Needs psutil
(dev dependency).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from contextlib import aclosing
from pathlib import Path
from typing import TypedDict, cast

from findex.crawler import CrawlConfig, CrawlStats, crawl


class CrawlRun(TypedDict):
    concurrency: int
    per_host: int
    delay: float
    pages: int
    requests: int
    errors: int
    wall: float
    pages_per_second: float
    peak_rss_mib: float


def _peak_rss_mib() -> float:
    import psutil

    mem = psutil.Process().memory_info()
    return float(getattr(mem, "peak_wset", mem.rss)) / 2**20


async def _run(seed: str, config: CrawlConfig) -> CrawlStats:
    stats = CrawlStats()
    with tempfile.TemporaryDirectory() as tmp, (Path(tmp) / "out.jsonl").open("w") as out:
        async with aclosing(crawl([seed], config, stats=stats)) as pages:
            async for page in pages:
                print(json.dumps({"url": page.url, "text": page.text}), file=out)
    return stats


def run_once(seed: str, max_pages: int, concurrency: int, per_host: int, delay: float) -> CrawlRun:
    config = CrawlConfig(
        max_pages=max_pages, concurrency=concurrency, per_host=per_host, delay=delay
    )
    stats = asyncio.run(_run(seed, config))
    return {
        "concurrency": concurrency,
        "per_host": per_host,
        "delay": delay,
        "pages": stats.pages,
        "requests": stats.requests,
        "errors": stats.errors,
        "wall": stats.elapsed,
        "pages_per_second": stats.pages_per_second,
        "peak_rss_mib": _peak_rss_mib(),
    }


CONFIGS: list[tuple[int, int, float]] = [  # concurrency, per_host, delay
    (1, 1, 0.0),
    (5, 5, 0.0),
    (20, 20, 0.0),
    (20, 2, 0.1),  # the polite default limits, same 20 workers
]


def table(runs: list[CrawlRun]) -> str:
    lines = [
        "| Concurrency | Per host / delay | Pages | Requests | Wall, s | Pages/s | Errors "
        "| Peak RSS |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        lines.append(
            f"| {r['concurrency']} | {r['per_host']} / {r['delay']:g} s | {r['pages']} | "
            f"{r['requests']} | **{r['wall']:.1f}** | **{r['pages_per_second']:.1f}** | "
            f"{r['errors']} | {r['peak_rss_mib']:.0f} MiB |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.bench_crawl", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("run", "sweep"):
        p = sub.add_parser(name)
        p.add_argument("seed")
        p.add_argument("--max-pages", type=int, default=200)
        if name == "run":
            p.add_argument("--concurrency", type=int, required=True)
            p.add_argument("--per-host", type=int, required=True)
            p.add_argument("--delay", type=float, default=0.0)
        else:
            p.add_argument("--out", type=Path, default=Path("bench/crawl_results.json"))
    args = parser.parse_args(argv)

    if args.cmd == "run":
        result = run_once(args.seed, args.max_pages, args.concurrency, args.per_host, args.delay)
        print(json.dumps(result))
        return 0
    runs: list[CrawlRun] = []
    for concurrency, per_host, delay in CONFIGS:
        cmd = [sys.executable, "-m", "findex.bench_crawl", "run", args.seed]
        cmd += ["--max-pages", str(args.max_pages), "--concurrency", str(concurrency)]
        cmd += ["--per-host", str(per_host), "--delay", str(delay)]
        done = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8")
        run = cast(CrawlRun, json.loads(done.stdout.strip().splitlines()[-1]))
        runs.append(run)
        print(
            f"c={concurrency} per_host={per_host}: {run['wall']:.1f} s", file=sys.stderr, flush=True
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(runs, indent=1), encoding="utf-8")
    print(table(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
