"""def vs async def for /search, and oha load tests (Lab 7).

The app here is the real one plus one extra route, ``/search-async``: the same
work as ``/search`` but declared ``async def`` — so it runs *on* the event loop
instead of in the thread pool. It exists only for this experiment; the real app
keeps the plain ``def``.

    # serve it (any number of workers):
    INDEX_PATH=crawl.fidx CORPUS_PATH=corpus/books-toscrape.jsonl \\
        uvicorn findex.bench_web:app --port 8001 --workers 1

    # 20 concurrent requests to each variant, repeated:
    python -m findex.bench_web concurrent http://127.0.0.1:8001 --q "science fiction"

    # one oha run, summarised as a Markdown table row:
    python -m findex.bench_web oha "http://127.0.0.1:8001/search?q=book" --label "1 worker, def"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Annotated, TypedDict

import httpx
from fastapi import Depends, HTTPException

from findex.query import QueryError
from findex.rank import Searcher
from findex.web.app import Page, Q, ScorerParam, create_app, get_searcher, run_search

app = create_app()


@app.get("/search-async", include_in_schema=False)
async def search_async(  # deliberately async def: blocks the event loop while it computes
    searcher: Annotated[Searcher, Depends(get_searcher)],
    q: Q,
    k: int = 10,
    page: Page = 1,
    scorer: ScorerParam = "bm25",
) -> dict[str, object]:
    try:
        results, total, took = run_search(searcher, q, k, page, scorer)
    except QueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"total": total, "took_ms": took, "results": [r.doc_id for r in results]}


# --- 20 concurrent requests -------------------------------------------------------------


class Burst(TypedDict):
    route: str
    wall_ms: float  # until the last of the 20 answers
    p50_ms: float
    max_ms: float
    health_max_ms: float  # slowest /health answer while the 20 searches were running


async def burst(base: str, route: str, q: str, n: int) -> Burst:
    async with httpx.AsyncClient(base_url=base, timeout=60) as client:
        await client.get(route, params={"q": q})  # warm-up (caches, connections)

        async def one() -> float:
            start = time.perf_counter()
            response = await client.get(route, params={"q": q})
            response.raise_for_status()
            return (time.perf_counter() - start) * 1000

        health: list[float] = []

        async def probe() -> None:  # what a platform health check sees meanwhile
            while True:
                t = time.perf_counter()
                await client.get("/health")
                health.append((time.perf_counter() - t) * 1000)
                await asyncio.sleep(0.05)

        prober = asyncio.create_task(probe())
        start = time.perf_counter()
        latencies = await asyncio.gather(*(one() for _ in range(n)))
        wall = (time.perf_counter() - start) * 1000
        prober.cancel()
    return {
        "route": route,
        "wall_ms": wall,
        "p50_ms": statistics.median(latencies),
        "max_ms": max(latencies),
        "health_max_ms": max(health, default=0.0),
    }


def concurrent(base: str, q: str, n: int, repeats: int) -> list[Burst]:
    rows: list[Burst] = []
    for route in ("/search", "/search-async"):
        runs = [asyncio.run(burst(base, route, q, n)) for _ in range(repeats)]
        rows.append(
            {
                "route": route,
                "wall_ms": statistics.median(r["wall_ms"] for r in runs),
                "p50_ms": statistics.median(r["p50_ms"] for r in runs),
                "max_ms": statistics.median(r["max_ms"] for r in runs),
                "health_max_ms": statistics.median(r["health_max_ms"] for r in runs),
            }
        )
    return rows


# --- oha --------------------------------------------------------------------------------


def oha_exe() -> str:
    """$OHA, else `oha` on PATH, else where winget puts it on Windows."""
    if os.environ.get("OHA"):
        return os.environ["OHA"]
    if found := shutil.which("oha"):
        return found
    packages = Path(os.environ.get("LOCALAPPDATA", ".")) / "Microsoft/WinGet/Packages"
    return str(next(packages.glob("hatoo.oha_*/oha.exe"), "oha"))


def run_oha(url: str, duration: str, connections: int) -> dict[str, float]:
    cmd = [
        oha_exe(),
        "-z",
        duration,
        "-c",
        str(connections),
        "--no-tui",
        "--output-format",
        "json",
        url,
    ]
    report = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    pct = report["latencyPercentiles"]
    codes = report.get("statusCodeDistribution", {})
    return {
        "rps": report["summary"]["requestsPerSec"],
        "p50": pct["p50"] * 1000,
        "p95": pct["p95"] * 1000,
        "p99": pct["p99"] * 1000,
        "ok": float(codes.get("200", 0)),
        "total": float(sum(codes.values())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.bench_web", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("concurrent")
    c.add_argument("base")
    c.add_argument("--q", default="book")
    c.add_argument("--n", type=int, default=20)
    c.add_argument("--repeats", type=int, default=5)
    o = sub.add_parser("oha")
    o.add_argument("url")
    o.add_argument("--label", required=True)
    o.add_argument("-z", "--duration", default="15s")
    o.add_argument("-c", "--connections", type=int, default=50)
    args = parser.parse_args(argv)

    if args.cmd == "concurrent":
        columns = f"{args.n} concurrent: wall, ms | p50, ms | slowest, ms | /health meanwhile, ms"
        print(f"| Route | {columns} |")
        print("|---|---|---|---|---|")
        for row in concurrent(args.base, args.q, args.n, args.repeats):
            cells = f"{row['wall_ms']:.0f} | {row['p50_ms']:.0f} | {row['max_ms']:.0f}"
            cells += f" | {row['health_max_ms']:.0f}"
            print(f"| `{row['route']}` | {cells} |")
    else:
        r = run_oha(args.url, args.duration, args.connections)
        print(
            f"| {args.label} | {args.connections} | {r['rps']:.0f} | {r['p50']:.1f} | "
            f"{r['p95']:.1f} | {r['p99']:.1f} | {int(r['ok'])}/{int(r['total'])} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
