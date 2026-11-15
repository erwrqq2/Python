"""What happens to the event loop if CPU work (indexing) runs inside a coroutine.

    uv run python scripts/blocking_demo.py data/crawl.jsonl

A heartbeat task wants to tick every 50 ms (think: the crawl's workers and the
live display). Meanwhile we build the index of the crawled pages three ways:

1. inline, inside the coroutine     -> the loop is frozen for the whole build;
                                       asyncio debug mode logs "Executing ... took N s"
2. asyncio.to_thread(build_index)   -> the loop keeps running, but the thread holds
                                       the GIL most of the time, so ticks get late
3. run_in_executor(ProcessPool)     -> the work is in another process; ticks on time

Prints the longest gap between two heartbeats for each variant.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from findex.corpus import iter_documents
from findex.index import build_index

TICK = 0.05


def build(path: Path) -> int:
    return len(build_index(iter_documents(path), positions=True))


async def heartbeat(gaps: list[float]) -> None:
    last = time.perf_counter()
    while True:
        await asyncio.sleep(TICK)
        now = time.perf_counter()
        gaps.append(now - last)
        last = now


async def measure(name: str, path: Path, how: str) -> None:
    gaps: list[float] = []
    beat = asyncio.create_task(heartbeat(gaps))
    await asyncio.sleep(0.2)  # let it tick normally first
    start = time.perf_counter()
    if how == "inline":
        terms = build(path)  # CPU work right inside the coroutine: nothing else can run
    elif how == "thread":
        terms = await asyncio.to_thread(build, path)
    else:
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(1) as pool:
            terms = await loop.run_in_executor(pool, build, path)
    took = time.perf_counter() - start
    await asyncio.sleep(0.2)
    beat.cancel()
    print(
        f"| {name} | {took:.2f} s | {len(gaps)} | {max(gaps) * 1000:.0f} ms | {terms:,} terms |",
        flush=True,
    )


async def main(path: Path) -> None:
    print("| variant | build | heartbeats | longest gap (want 50 ms) | |")
    print("|---|---|---|---|---|")
    await measure("inline in a coroutine", path, "inline")
    await measure("asyncio.to_thread", path, "thread")
    await measure("ProcessPoolExecutor", path, "process")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/crawl.jsonl")), debug=True)
