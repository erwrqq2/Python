"""The `findex` command (typer + rich).

    findex index data/ --out index.fidx --positions     # build, with a progress bar
    findex search index.fidx 'whale AND (harpoon OR lance)' -k 5
    findex search index.fidx telegraph --json | jq .title
    findex stats index.fidx          # or: findex stats data/
    findex -vv search ...            # INFO / DEBUG logs on stderr

Streams: results go to stdout, everything diagnostic (logs, progress bar,
errors) to stderr, so ``--json`` output can be piped. Expected failures — a
missing file, a broken query, a phrase on an index without positions — print
one line and exit with code 1, no traceback.

Logging is configured here, once, in the app callback; library modules only
call ``logging.getLogger(__name__)``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from itertools import islice
from pathlib import Path
from typing import Annotated, Final, TypedDict

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from findex.corpus import Document, count_documents, iter_documents
from findex.engines import EngineName
from findex.index import Index, build_index
from findex.parallel import ExecutorName, build_parallel
from findex.query import QueryError, parse
from findex.rank import ScorerName, Searcher, SearchResult, get_scorer
from findex.stats import compute_stats
from findex.store import Format, IndexFormatError, open_index, save

log = logging.getLogger(__name__)

out = Console()  # results
err = Console(stderr=True)  # logs, progress, errors

app = typer.Typer(
    name="findex",
    help="A small full-text search engine: build an index, search it, inspect it.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


# --- choices (typer needs Enums; the library takes Literal strings) ---------------------


class ScorerChoice(StrEnum):
    bm25 = "bm25"
    tfidf = "tfidf"


class EngineChoice(StrEnum):
    set = "set"
    merge = "merge"


class ExecutorChoice(StrEnum):
    serial = "serial"
    threads = "threads"
    processes = "processes"


class FormatChoice(StrEnum):
    binary = "binary"
    pickle = "pickle"


SCORER_NAMES: Final[dict[ScorerChoice, ScorerName]] = {
    ScorerChoice.bm25: "bm25",
    ScorerChoice.tfidf: "tfidf",
}
ENGINE_NAMES: Final[dict[EngineChoice, EngineName]] = {
    EngineChoice.set: "set",
    EngineChoice.merge: "merge",
}
EXECUTOR_NAMES: Final[dict[ExecutorChoice, ExecutorName]] = {
    ExecutorChoice.serial: "serial",
    ExecutorChoice.threads: "threads",
    ExecutorChoice.processes: "processes",
}
# Processes win on a multi-core machine (see README, Lab 5); threads do not under the GIL.
DEFAULT_EXECUTOR: Final = ExecutorChoice.processes
FORMAT_NAMES: Final[dict[FormatChoice, Format]] = {
    FormatChoice.binary: "binary",
    FormatChoice.pickle: "pickle",
}


# --- errors and logging -----------------------------------------------------------------


class CliError(Exception):
    """An expected failure: reported as one line, exit code 1."""


@contextmanager
def expected_errors() -> Generator[None]:
    """Turn known failure modes into one red line on stderr and exit code 1."""
    try:
        yield
    except CliError as exc:
        message = str(exc)
    except QueryError as exc:
        message = f"bad query: {exc}"
    except FileNotFoundError as exc:
        message = f"file not found: {exc.filename}"
    except IndexFormatError as exc:
        log.debug("load failed", exc_info=True)  # full chain with -vv
        message = str(exc)
    else:
        return
    err.print(f"[bold red]error:[/] {escape(message)}", soft_wrap=True)  # never wrap: one line
    raise typer.Exit(code=1)


def setup_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    handler = RichHandler(console=err, show_time=False, show_path=False, markup=False)
    logging.basicConfig(level=level, format="%(name)s: %(message)s", handlers=[handler], force=True)


def _version_callback(value: bool) -> None:
    if value:
        try:
            out.print(f"findex {version('findex')}")
        except PackageNotFoundError:  # pragma: no cover - running from a bare checkout
            out.print("findex (not installed)")
        raise typer.Exit


@app.callback()
def main(
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="-v: INFO, -vv: DEBUG (stderr).")
    ] = 0,
    show_version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """A small full-text search engine: build an index, search it, inspect it."""
    setup_logging(verbose)


# --- findex index -----------------------------------------------------------------------


def _with_progress(
    docs: Iterable[Document], progress: Progress, total: int | None
) -> Iterator[Document]:
    task = progress.add_task("indexing", total=total)
    for doc in docs:
        yield doc
        progress.advance(task)


@app.command("index")
def index_cmd(
    corpus: Annotated[Path, typer.Argument(help="Corpus directory or .txt/.jsonl file.")],
    out_path: Annotated[Path, typer.Option("--out", "-o", help="Where to save the index.")],
    fmt: Annotated[
        FormatChoice, typer.Option("--format", help="File format.")
    ] = FormatChoice.binary,
    positions: Annotated[
        bool, typer.Option("--positions", help="Store token positions (needed for phrases).")
    ] = False,
    limit: Annotated[int | None, typer.Option(help="Index only the first N documents.")] = None,
    executor: Annotated[
        ExecutorChoice, typer.Option(help="How to run build_partial over the chunks.")
    ] = DEFAULT_EXECUTOR,
    workers: Annotated[
        int | None, typer.Option(min=1, help="Threads/processes [default: CPU count].")
    ] = None,
) -> None:
    """Build an index from a corpus and save it (in parallel by default)."""
    with expected_errors():
        if not corpus.exists():
            raise CliError(f"corpus not found: {corpus}")
        total = count_documents(corpus)
        if total is not None and limit is not None:
            total = min(total, limit)
        columns = (
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        )
        start = time.perf_counter()
        merge_seconds: float | None = None
        with Progress(*columns, console=err, transient=True) as progress:
            if total is None:  # JSON-lines corpus: documents are lines, not files -> serial
                log.warning("JSON-lines corpus: building serially")
                docs = _with_progress(islice(iter_documents(corpus), limit), progress, None)
                index = build_index(docs, positions=positions)
                used = "serial (json-lines)"
            else:
                task = progress.add_task("indexing", total=total)
                index, stats = build_parallel(
                    corpus,
                    executor=EXECUTOR_NAMES[executor],
                    workers=workers,
                    positions=positions,
                    limit=limit,
                    on_chunk_done=lambda n: progress.advance(task, n),
                )
                merge_seconds = stats.merge_seconds
                used = f"{stats.executor}, {stats.workers} workers, {stats.chunks} chunks"
        built = time.perf_counter()
        save(index, out_path, FORMAT_NAMES[fmt])
        saved = time.perf_counter()

    merge_note = f" (merge {merge_seconds:.1f} s)" if merge_seconds is not None else ""
    table = Table(show_header=False, box=None)
    table.add_row("index", f"{out_path} ({out_path.stat().st_size / 2**20:.1f} MiB, {fmt.value})")
    table.add_row("documents", f"{index.num_docs:,}")
    table.add_row("terms", f"{len(index):,}")
    table.add_row("postings", f"{index.num_postings:,}")
    table.add_row("positions", "yes" if positions else "no")
    table.add_row("build", used)
    table.add_row("time", f"build {built - start:.1f} s{merge_note}, save {saved - built:.1f} s")
    out.print(table)


# --- findex search ----------------------------------------------------------------------


class SearchHit(TypedDict):
    """One line of `findex search --json`."""

    rank: int
    score: float
    doc_id: int
    title: str
    path: str
    snippet: str


_BOLD_RE: Final = re.compile(r"\*\*(.+?)\*\*")


def highlight(snippet: str) -> str:
    """Snippet with **term** markers -> rich markup (the rest escaped)."""
    return _BOLD_RE.sub(r"[bold yellow]\1[/]", escape(snippet))


def _hit(rank: int, result: SearchResult, index: Index) -> SearchHit:
    return {
        "rank": rank,
        "score": round(result.score, 4),
        "doc_id": result.doc_id,
        "title": result.title,
        "path": index.doc_meta[result.doc_id].path,
        "snippet": result.snippet,
    }


@app.command("search")
def search_cmd(
    index_path: Annotated[Path, typer.Argument(metavar="INDEX", help="Index file.")],
    query: Annotated[
        str, typer.Argument(help="e.g. 'whale AND (harpoon OR lance) NOT \"civil war\"'")
    ],
    k: Annotated[int, typer.Option("-k", "--k", min=1, help="How many results.")] = 10,
    scorer: Annotated[ScorerChoice, typer.Option(help="Ranking function.")] = ScorerChoice.bm25,
    engine: Annotated[
        EngineChoice, typer.Option(help="Boolean matching engine.")
    ] = EngineChoice.set,
    as_json: Annotated[bool, typer.Option("--json", help="JSON lines on stdout.")] = False,
    snippets: Annotated[bool, typer.Option(help="Show snippets.")] = True,
) -> None:
    """Search an index; results are ranked, with highlighted snippets."""
    with expected_errors():
        parse(query)  # fail fast on a broken query, before loading the index
        with open_index(index_path) as index:
            searcher = Searcher(index, ENGINE_NAMES[engine])
            start = time.perf_counter()
            results = searcher.search(
                query, get_scorer(SCORER_NAMES[scorer]), k=k, snippets=snippets
            )
            elapsed = time.perf_counter() - start
            hits = [_hit(rank, r, index) for rank, r in enumerate(results, start=1)]
            matched, num_docs = searcher.last_match_count, index.num_docs

    log.info("%d of %d documents match, search took %.1f ms", matched, num_docs, elapsed * 1000)
    if as_json:
        for hit in hits:
            typer.echo(json.dumps(hit, ensure_ascii=False))
        return

    table = Table(
        title=f"{escape(query)}  [dim]({matched} of {num_docs} match, {scorer.value})[/]",
        title_justify="left",
        expand=True,
    )
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("score", justify="right", no_wrap=True)
    table.add_column("document", ratio=1, overflow="fold")
    for hit in hits:
        text = f"[bold]{escape(hit['title'])}[/] [dim]\\[{hit['doc_id']}][/]"
        if hit["snippet"]:
            text += "\n" + highlight(hit["snippet"])
        table.add_row(str(hit["rank"]), f"{hit['score']:.3f}", text)
    out.print(table)


# --- findex stats -----------------------------------------------------------------------


def _term_counts(index: Index) -> Counter[str]:
    return Counter({term: sum(p.tf for p in plist) for term, plist in index.items()})


@app.command("stats")
def stats_cmd(
    path: Annotated[Path, typer.Argument(help="Index file, or a corpus directory (Lab 1 pass).")],
    top: Annotated[int, typer.Option(help="How many top terms.")] = 20,
) -> None:
    """Documents, tokens, vocabulary and top terms of an index or a corpus."""
    with expected_errors():
        if not path.exists():
            raise CliError(f"file not found: {path}")
        if path.is_dir():
            stats = compute_stats(iter_documents(path))
            documents, tokens, counts = stats.documents, stats.tokens, stats.counts
        else:
            with open_index(path) as index:
                documents = index.num_docs
                tokens = sum(index.doc_lengths.values())
                counts = _term_counts(index)

    summary = Table(show_header=False, box=None)
    summary.add_row("documents", f"{documents:,}")
    summary.add_row("tokens", f"{tokens:,}")
    summary.add_row("vocabulary", f"{len(counts):,}")
    out.print(summary)
    terms = Table(title=f"top {top} terms", title_justify="left")
    terms.add_column("#", justify="right", style="dim")
    terms.add_column("term")
    terms.add_column("count", justify="right")
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top]  # ties: a-z
    for rank, (term, count) in enumerate(ranked, start=1):
        terms.add_row(str(rank), escape(term), f"{count:,}")
    out.print(terms)
