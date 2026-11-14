"""Benchmark of the parallel index build (Lab 5).

    # one measurement, in this process (prints one JSON line):
    python -m findex.bench_parallel run data/ --executor processes --workers 8

    # every configuration, each run in a fresh subprocess, appended to a JSON file:
    python -m findex.bench_parallel sweep data/ --runs 3 --out bench/results.json
    python -m findex.bench_parallel sweep data/ --runs 3 --out bench/results.json \
        --python .venv-ft/Scripts/python.exe          # same with free-threaded 3.13t

    # median table (Markdown) and the speedup plot:
    python -m findex.bench_parallel report bench/results.json --plot docs/speedup.png

Why a fresh process per run: peak RSS is a high-water mark for the whole life
of a process, so two runs in one process would share it. A warm-up build runs
first (puts the corpus in the OS file cache); configurations are interleaved
run by run so slow drift (temperature, clock) spreads over all of them.

CPU time = parent CPU (all its threads) + CPU of the worker processes, read
with psutil while they are still alive (`before_shutdown` hook) — this
includes their interpreter start-up and pickling, which `build_partial`'s own
`process_time` would miss.

Needs the dev dependencies (psutil, matplotlib for the plot).
"""

# matplotlib's own annotations are incomplete (many members are partially Unknown);
# relax just that rule for this benchmark/plotting module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Final, TypedDict, cast

from findex.parallel import EXECUTOR_NAMES, ExecutorName, build_parallel

DEFAULT_WORKERS: Final = (1, 2, 4, 8, os.cpu_count() or 8)


class RunResult(TypedDict):
    python: str  # e.g. "3.13.18" or "3.13.18t"
    gil: bool  # sys._is_gil_enabled() after all imports
    executor: ExecutorName
    workers: int
    chunks: int
    wall: float
    merge: float
    cpu: float
    parent_peak_rss: int  # bytes
    workers_peak_rss: int  # bytes, sum over worker processes (0 for serial/threads)
    terms: int


def _peak_rss(process: object) -> int:
    import psutil

    mem = cast(psutil.Process, process).memory_info()
    # Windows reports the peak working set; elsewhere fall back to the current RSS.
    return int(getattr(mem, "peak_wset", mem.rss))


def run_once(root: Path, executor: ExecutorName, workers: int) -> RunResult:
    import psutil

    me = psutil.Process()
    start = me.cpu_times()
    sampled = {"cpu": 0.0, "rss": 0}

    def sample_workers() -> None:
        for child in me.children(recursive=True):
            try:
                times = child.cpu_times()
                sampled["cpu"] += times.user + times.system
                sampled["rss"] += _peak_rss(child)
            except psutil.NoSuchProcess:  # pragma: no cover - a worker that already exited
                pass

    index, stats = build_parallel(
        root, executor=executor, workers=workers, before_shutdown=sample_workers
    )
    end = me.cpu_times()
    parent_cpu = (end.user - start.user) + (end.system - start.system)
    free_threaded = not sys._is_gil_enabled()  # pyright: ignore[reportPrivateUsage] - documented API, underscore by design
    return {
        "python": platform.python_version() + ("t" if free_threaded else ""),
        "gil": not free_threaded,
        "executor": executor,
        "workers": stats.workers,
        "chunks": stats.chunks,
        "wall": stats.wall_seconds,
        "merge": stats.merge_seconds,
        "cpu": parent_cpu + sampled["cpu"],
        "parent_peak_rss": _peak_rss(me),
        "workers_peak_rss": int(sampled["rss"]),
        "terms": len(index),
    }


def sweep(
    root: Path,
    python: str,
    executors: list[ExecutorName],
    workers: list[int],
    runs: int,
    out: Path,
) -> None:
    configs: list[tuple[ExecutorName, int]] = []
    if "serial" in executors:
        configs.append(("serial", 1))
    for executor in executors:
        if executor != "serial":
            configs.extend((executor, n) for n in workers)
    results: list[RunResult] = json.loads(out.read_text("utf-8")) if out.exists() else []

    python = str(Path(python).resolve())  # CreateProcess on Windows wants an absolute path

    def measure(executor: ExecutorName, n: int) -> RunResult:
        cmd = [python, "-m", "findex.bench_parallel", "run", str(root)]
        cmd += ["--executor", executor, "--workers", str(n)]
        done = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8")
        return cast(RunResult, json.loads(done.stdout.strip().splitlines()[-1]))

    print(f"warm-up ({python})", file=sys.stderr, flush=True)
    measure("serial", 1)
    for run in range(1, runs + 1):
        for executor, n in configs:
            result = measure(executor, n)
            results.append(result)
            print(
                f"run {run}/{runs} {result['python']} {executor:>9} x{n:<2} "
                f"wall {result['wall']:.2f}s cpu {result['cpu']:.2f}s merge {result['merge']:.2f}s",
                file=sys.stderr,
                flush=True,
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(results, indent=1), encoding="utf-8")


class Row(TypedDict):
    python: str
    executor: ExecutorName
    workers: int
    n: int
    wall: float
    wall_min: float
    wall_max: float
    cpu: float
    parent_rss: float  # MiB
    workers_rss: float  # MiB
    merge: float
    speedup: float  # vs serial on the same interpreter
    speedup_vs_gil: float  # vs serial on the regular (GIL) interpreter


def summarize(results: list[RunResult]) -> list[Row]:
    groups: dict[tuple[str, ExecutorName, int], list[RunResult]] = {}
    for r in results:
        groups.setdefault((r["python"], r["executor"], r["workers"]), []).append(r)
    med = statistics.median
    serial_wall = {
        py: med(x["wall"] for x in rs) for (py, ex, _), rs in groups.items() if ex == "serial"
    }
    gil_serial = next((w for py, w in serial_wall.items() if not py.endswith("t")), None)
    rows: list[Row] = []
    for (py, ex, w), rs in sorted(
        groups.items(),
        key=lambda kv: (kv[0][0].endswith("t"), EXECUTOR_NAMES.index(kv[0][1]), kv[0][2]),
    ):
        wall = med(r["wall"] for r in rs)
        rows.append(
            {
                "python": py,
                "executor": ex,
                "workers": w,
                "n": len(rs),
                "wall": wall,
                "wall_min": min(r["wall"] for r in rs),
                "wall_max": max(r["wall"] for r in rs),
                "cpu": med(r["cpu"] for r in rs),
                "parent_rss": med(r["parent_peak_rss"] for r in rs) / 2**20,
                "workers_rss": med(r["workers_peak_rss"] for r in rs) / 2**20,
                "merge": med(r["merge"] for r in rs),
                "speedup": serial_wall.get(py, wall) / wall,
                "speedup_vs_gil": (gil_serial or wall) / wall,
            }
        )
    return rows


def markdown_table(rows: list[Row]) -> str:
    lines = [
        "| Python | Executor | Workers | Wall, s (median) | min–max | CPU, s | Peak RSS parent | "
        "Workers' RSS (sum) | Merge, s | Speedup | vs GIL serial |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        workers_rss = f"{r['workers_rss']:.0f} MiB" if r["workers_rss"] else "—"
        cells = [
            r["python"],
            r["executor"],
            str(r["workers"]),
            f"**{r['wall']:.2f}**",
            f"{r['wall_min']:.2f}–{r['wall_max']:.2f}",
            f"{r['cpu']:.1f}",
            f"{r['parent_rss']:.0f} MiB",
            workers_rss,
            f"{r['merge']:.2f}",
            f"**{r['speedup']:.2f}×**",
            f"{r['speedup_vs_gil']:.2f}×",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def plot(rows: list[Row], path: Path, cpu_count: int, amdahl: float | None = None) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.5, 4.8), dpi=130)
    max_workers = max(r["workers"] for r in rows)
    ax.plot([1, max_workers], [1, max_workers], "--", color="0.6", label="ideal (linear)")
    if amdahl:
        xs = [x / 4 for x in range(4, 4 * max_workers + 1)]
        ys = [1 / (amdahl + (1 - amdahl) / x) for x in xs]
        ax.plot(xs, ys, ":", color="0.35", label=f"Amdahl, serial part {amdahl:.0%}")
    styles = {"threads": "o-", "processes": "s-"}
    for py in sorted({r["python"] for r in rows}, key=lambda p: p.endswith("t")):
        for ex in ("threads", "processes"):
            pts = sorted(
                (r["workers"], r["speedup_vs_gil"])
                for r in rows
                if r["python"] == py and r["executor"] == ex
            )
            if pts:
                label = f"{ex}, Python {py}" + (" (no GIL)" if py.endswith("t") else " (GIL)")
                ax.plot([p[0] for p in pts], [p[1] for p in pts], styles[ex], label=label)
    ax.axvline(cpu_count / 2, color="0.85", lw=1)
    ax.text(
        cpu_count / 2,
        0.03,
        f" {cpu_count // 2} physical cores (SMT beyond)",
        color="0.5",
        fontsize=8,
        transform=ax.get_xaxis_transform(),
    )
    ax.set_xlabel("workers")
    ax.set_ylabel("speedup vs serial build (Python 3.13, GIL)")
    ax.set_title("findex: parallel index build, 143 books / 61 MB")
    ax.set_xticks(sorted({r["workers"] for r in rows}))
    ax.set_ylim(0, 6.5)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findex.bench_parallel", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("root", type=Path)
    run_p.add_argument("--executor", choices=EXECUTOR_NAMES, required=True)
    run_p.add_argument("--workers", type=int, required=True)
    sweep_p = sub.add_parser("sweep")
    sweep_p.add_argument("root", type=Path)
    sweep_p.add_argument("--python", default=sys.executable)
    sweep_p.add_argument(
        "--executors", nargs="+", choices=EXECUTOR_NAMES, default=list(EXECUTOR_NAMES)
    )
    sweep_p.add_argument("--workers", nargs="+", type=int, default=list(DEFAULT_WORKERS))
    sweep_p.add_argument("--runs", type=int, default=3)
    sweep_p.add_argument("--out", type=Path, default=Path("bench/results.json"))
    report_p = sub.add_parser("report")
    report_p.add_argument("results", type=Path)
    report_p.add_argument("--plot", type=Path)
    report_p.add_argument("--amdahl", type=float, help="serial fraction for a reference curve")
    args = parser.parse_args(argv)

    if args.cmd == "run":
        print(json.dumps(run_once(args.root, args.executor, args.workers)))
    elif args.cmd == "sweep":
        sweep(args.root, args.python, args.executors, args.workers, args.runs, args.out)
    else:
        rows = summarize(cast(list[RunResult], json.loads(args.results.read_text("utf-8"))))
        print(markdown_table(rows))
        if args.plot:
            plot(rows, args.plot, os.cpu_count() or 1, args.amdahl)
            print(f"\nplot: {args.plot}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
