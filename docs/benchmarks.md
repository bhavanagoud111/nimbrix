# Benchmarks

Measured 2026-10-10 on one developer machine. These are real measurements of this code on
this hardware, not capacity claims; production numbers depend on instance sizes, data shape,
and network.

## Environment

| Item | Value |
| --- | --- |
| Host | Apple M4 (10 cores), 24 GB RAM, macOS (Darwin 27.0.0) |
| Containers | Docker 29.8.2, Docker Desktop VM with 10 CPUs / 7.7 GB RAM |
| Stack | `make demo`: PostgreSQL 16.10, API (uvicorn, 1 process), worker (1 process), web (Next.js standalone) |
| Data | Tenant `acme-demo`: 7,760 line items (120 days). Tenant `globex-demo` after benchmark imports: 83,440 line items |
| Harness | `scripts/benchmarks/run_benchmarks.py` (raw output: [`results/benchmarks.json`](results/benchmarks.json)); `tests/performance/load_cost_queries.py` ([`results/query-latency-globex.json`](results/query-latency-globex.json)) |
| Rate limit | Raised to 100,000/min for the run (`NIMBRIX_RATE_LIMIT_REQUESTS_PER_MINUTE`); every request returned 200 |

## Ingestion throughput (worker time, `started_at` → `finished_at`)

| Import | Rows | Size | Time | Throughput |
| --- | --- | --- | --- | --- |
| Synthetic 365-day dataset (+68 resources, budgets) | 23,440 | — | 7.02 s | 3,339 rows/s |
| CSV upload (validated row by row, upserted) | 60,000 | 9.0 MB | 20.64 s | 2,906 rows/s |

## Cost-query latency (API directly, 500 requests per endpoint, concurrency 10, after warm-up)

| Endpoint | Tenant rows | p50 | p95 | p99 | Throughput |
| --- | --- | --- | --- | --- | --- |
| `GET /costs/summary` (30-day window) | 7,760 | 61.6 ms | 80.2 ms | 143.8 ms | 157 req/s |
| `GET /costs/timeseries` 90 days by service | 7,760 | 57.6 ms | 110.0 ms | 122.4 ms | 157 req/s |
| `GET /costs/breakdown` by team | 7,760 | 40.3 ms | 56.1 ms | 88.3 ms | 239 req/s |
| `GET /costs/breakdown` by resource | 7,760 | 40.9 ms | 53.2 ms | 61.8 ms | 240 req/s |
| `GET /costs/timeseries` monthly by account | 7,760 | 38.5 ms | 53.6 ms | 92.5 ms | 248 req/s |
| `GET /costs/summary` (30-day window) | 83,440 | 172.1 ms | 199.6 ms | 218.3 ms | 57 req/s |
| `GET /costs/timeseries` 90 days by service | 83,440 | 71.1 ms | 126.7 ms | 145.2 ms | 130 req/s |
| `GET /costs/breakdown` by team | 83,440 | 64.5 ms | 90.4 ms | 99.6 ms | 159 req/s |
| `GET /costs/breakdown` by resource | 83,440 | 74.8 ms | 127.0 ms | 140.5 ms | 122 req/s |
| `GET /costs/timeseries` monthly by account | 83,440 | 65.9 ms | 83.3 ms | 92.1 ms | 149 req/s |

The summary endpoint issues several aggregates (window, prior window, allocation, month-to-date,
previous month, drivers, other currencies) and is the slowest; a materialized daily rollup is
the obvious next optimization if tenants grow well beyond this size.

## Forecast quality and run time

Offline evaluation (synthetic labeled series, details in [ml.md](ml.md#measured-results)):
Holt-Winters 30-day WAPE 3.50% vs seasonal-naive 4.81%; 80% intervals covered 83.3%.

On the `acme-demo` tenant (real run through the API):

| Horizon | Job time | Selected model | Backtest WAPE (HW / naive) | Interval coverage |
| --- | --- | --- | --- | --- |
| 7 days | 0.36 s | Holt-Winters | 6.41% / 6.57% | 83.3% / 83.3% |
| 30 days | 0.32 s | Holt-Winters | 2.12% / 2.48% | 83.3% / 83.3% |

## Simulated workflow (end to end through the web origin)

| Step | Time |
| --- | --- |
| Investigate the EC2 anomaly → report completed (7 graph steps, deterministic mode) | 0.85 s |
| Request approval → approve → execute simulation → verified | 1.15 s |
| **Total** | **2.0 s** |

Times include API → job queue → worker polling (1 s poll interval), which dominates.

## Reproduce

```bash
make demo
NIMBRIX_RATE_LIMIT_REQUESTS_PER_MINUTE=100000 docker compose up -d --wait api
cd apps/api && uv run python ../../scripts/benchmarks/run_benchmarks.py --output ../../docs/results/benchmarks.json
uv run python ../../tests/performance/load_cost_queries.py --email admin@globex.demo --requests 500 --concurrency 10
```
