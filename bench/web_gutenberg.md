### index.fidx, query "telegraph"

| Route | 20 concurrent: wall, ms | p50, ms | slowest, ms |
|---|---|---|---|
| `/search` | 1574 | 1538 | 1566 |
| `/search-async` | 1576 | 816 | 1563 |

| Target | Concurrency | RPS | p50, ms | p95, ms | p99, ms | 200 OK |
|---|---|---|---|---|---|---|
| local, 1 worker, async def | 50 | 16 | 3447.5 | 7947.9 | 10086.6 | 193/193 |
| local, 1 worker, def | 50 | 14 | 4063.7 | 8913.0 | 9686.8 | 154/154 |
| local, 4 workers, def | 50 | 47 | 1083.6 | 1331.1 | 1448.1 | 658/658 |
