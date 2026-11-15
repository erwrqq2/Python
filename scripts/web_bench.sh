#!/usr/bin/env bash
# Lab 7 measurements: def vs async def (20 concurrent) and oha, for one index.
#   scripts/web_bench.sh crawl.fidx corpus/books-toscrape.jsonl book        > bench/web_crawl.md
#   scripts/web_bench.sh index.fidx "" telegraph                            > bench/web_gutenberg.md
set -euo pipefail
INDEX=$1; CORPUS=$2; QUERY=$3
PY=.venv/Scripts/python.exe
[ -x "$PY" ] || PY=.venv/bin/python
export INDEX_PATH=$INDEX
if [ -n "$CORPUS" ]; then export CORPUS_PATH=$CORPUS; else unset CORPUS_PATH || true; fi

start() {  # start uvicorn with N workers on port P, wait for /health
  "$PY" -m uvicorn findex.bench_web:app --port "$2" --workers "$1" --log-level warning --no-access-log &
  SERVER=$!
  for _ in $(seq 1 120); do curl -sf "http://127.0.0.1:$2/health" >/dev/null && return 0; sleep 0.5; done
  echo "server did not start" >&2; exit 1
}
SERVER=""
stop() {
  if [ -n "$SERVER" ]; then
    if [ -r "/proc/$SERVER/winpid" ]; then
      # Windows: the venv python.exe is a launcher with the real interpreter as a child
      # (plus uvicorn's workers) — kill the whole tree, not just the launcher.
      taskkill //PID "$(cat "/proc/$SERVER/winpid")" //T //F >/dev/null 2>&1 || true
    else
      kill "$SERVER" 2>/dev/null || true
    fi
    wait "$SERVER" 2>/dev/null || true
  fi
  SERVER=""; sleep 2
}
trap stop EXIT  # never leave a server running, even when a step fails

echo "### $INDEX, query \"$QUERY\""
echo
start 1 8101
"$PY" -m findex.bench_web concurrent http://127.0.0.1:8101 --q "$QUERY"
echo
echo "| Target | Concurrency | RPS | p50, ms | p95, ms | p99, ms | 200 OK |"
echo "|---|---|---|---|---|---|---|"
"$PY" -m findex.bench_web oha "http://127.0.0.1:8101/search-async?q=$QUERY" --label "local, 1 worker, async def"
"$PY" -m findex.bench_web oha "http://127.0.0.1:8101/search?q=$QUERY" --label "local, 1 worker, def"
stop
start 4 8104
"$PY" -m findex.bench_web oha "http://127.0.0.1:8104/search?q=$QUERY" --label "local, 4 workers, def"
stop
