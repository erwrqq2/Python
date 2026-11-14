"""Lost updates with shared state in threads — on the GIL build and on free-threaded 3.13t.

    .venv/Scripts/python.exe    scripts/race_demo.py      # Python 3.13, GIL
    .venv-ft/Scripts/python.exe scripts/race_demo.py      # Python 3.13t, no GIL

8 threads each do 200,000 updates to one shared object. Expected total: 1,600,000.

* ``counter += 1`` on a global int  — LOAD_GLOBAL, BINARY_OP(+=), STORE_GLOBAL: three
  steps; a switch between the load and the store loses the other thread's update.
* ``counts[term] += 1`` on a shared Counter — what a naive threaded indexer would do.
* ``shared.append(x)`` on a list — one C call; a single list operation is atomic
  (the GIL, or on 3.13t the list's own internal lock), so nothing is lost.
* fixes: a ``threading.Lock`` around the read-modify-write, or no shared state at all —
  every thread fills its own Counter and they are merged at the end (what
  `findex.parallel.build_partial` + `merge` do).
"""

from __future__ import annotations

import dis
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable

THREADS = 8
ITERATIONS = 200_000
EXPECTED = THREADS * ITERATIONS
WORDS = ["whale", "ship", "sea", "the", "harpoon", "captain", "deck", "storm"]

counter = 0


def run(worker: Callable[[int], None]) -> float:
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.perf_counter() - start


def unsafe_int(_: int) -> None:
    global counter
    for _ in range(ITERATIONS):
        counter += 1


lock = threading.Lock()


def locked_int(_: int) -> None:
    global counter
    for _ in range(ITERATIONS):
        with lock:
            counter += 1


shared_counts: Counter[str] = Counter()


def unsafe_counter(i: int) -> None:
    for k in range(ITERATIONS):
        shared_counts[WORDS[(i + k) % len(WORDS)]] += 1


per_thread: list[Counter[str]] = [Counter() for _ in range(THREADS)]


def private_counter(i: int) -> None:
    mine = per_thread[i]  # no sharing while counting...
    for k in range(ITERATIONS):
        mine[WORDS[(i + k) % len(WORDS)]] += 1


shared_list: list[int] = []


def list_append(i: int) -> None:
    for k in range(ITERATIONS):
        shared_list.append(k)


def report(name: str, total: int, seconds: float) -> None:
    lost = EXPECTED - total
    verdict = "OK" if lost == 0 else f"LOST {lost:,} ({lost / EXPECTED:.1%})"
    print(f"| {name} | {total:,} | {verdict} | {seconds:.2f} |")


def main() -> None:
    global counter
    gil = sys._is_gil_enabled()  # pyright: ignore[reportPrivateUsage] - documented API, underscore by design
    print(f"Python {sys.version.split()[0]}, sys._is_gil_enabled() = {gil}")
    print(f"{THREADS} threads x {ITERATIONS:,} updates, expected {EXPECTED:,}\n")
    print("| variant | total | result | seconds |")
    print("|---|---|---|---|")

    counter = 0
    seconds = run(unsafe_int)  # run first: arguments are evaluated left to right
    report("`counter += 1` (global int)", counter, seconds)
    counter = 0
    seconds = run(locked_int)
    report("`counter += 1` under `Lock`", counter, seconds)

    seconds = run(unsafe_counter)
    report("`counts[w] += 1` (shared Counter)", sum(shared_counts.values()), seconds)
    seconds = run(private_counter)
    merged: Counter[str] = Counter()
    for mine in per_thread:  # ...one merge at the end
        merged.update(mine)
    report("Counter per thread + merge", sum(merged.values()), seconds)

    seconds = run(list_append)
    report("`shared.append(x)` (list)", len(shared_list), seconds)

    print("\n`counter += 1` compiles to:")
    dis.dis(compile("counter += 1", "<demo>", "exec"))


if __name__ == "__main__":
    main()
