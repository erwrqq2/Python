### crawl.fidx, query "book"

| Route | 20 concurrent: wall, ms | p50, ms | slowest, ms |
|---|---|---|---|
| `/search` | 74 | 53 | 63 |
| `/search-async` | 66 | 47 | 55 |

| Target | Concurrency | RPS | p50, ms | p95, ms | p99, ms | 200 OK |
|---|---|---|---|---|---|---|
| local, 1 worker, async def | 50 | 519 | 96.3 | 109.5 | 131.4 | 7744/7744 |
| local, 1 worker, def | 50 | 520 | 95.8 | 107.5 | 125.1 | 7747/7747 |
| local, 4 workers, def | 50 | 1727 | 28.8 | 37.3 | 40.6 | 25882/25882 |
