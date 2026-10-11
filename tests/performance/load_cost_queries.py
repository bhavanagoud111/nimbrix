"""Concurrent load test for cost analytics endpoints.

Run against a running stack (API port by default):

    cd apps/api && uv run python ../../tests/performance/load_cost_queries.py \
        --base-url http://127.0.0.1:8200 --requests 300 --concurrency 10

Reports per-endpoint latency percentiles, throughput, and non-200 counts as JSON. The rate
limiter counts against these requests; raise NIMBRIX_RATE_LIMIT_REQUESTS_PER_MINUTE for the
API when measuring, and report any 429s rather than hiding them.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx2

ENDPOINTS = {
    "summary": "/api/v1/costs/summary",
    "timeseries_90d_by_service": "/api/v1/costs/timeseries?group_by=service&top=8",
    "breakdown_by_team": "/api/v1/costs/breakdown?dimension=team",
    "breakdown_by_resource": "/api/v1/costs/breakdown?dimension=resource&limit=20",
    "monthly_by_account": "/api/v1/costs/timeseries?granularity=month&group_by=account&start=2026-06-01",
}


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[index]


def login(base: str, email: str, password: str) -> str:
    response = httpx2.post(f"{base}/api/v1/auth/login", json={"email": email, "password": password}, timeout=10)
    response.raise_for_status()
    token = response.cookies.get("nimbrix_session")
    if not token:
        raise SystemExit("login did not return a session cookie")
    return token


def run(base: str, token: str, path: str, requests: int, concurrency: int) -> dict[str, Any]:
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    client = httpx2.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def one(_: int) -> tuple[int, float]:
        started = time.perf_counter()
        response = client.get(path)
        return response.status_code, (time.perf_counter() - started) * 1000

    client.get(path)  # warm-up (not measured)
    wall = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for status, ms in pool.map(one, range(requests)):
            statuses[status] = statuses.get(status, 0) + 1
            if status == 200:
                latencies.append(ms)
    elapsed = time.perf_counter() - wall
    client.close()
    return {
        "path": path,
        "requests": requests,
        "concurrency": concurrency,
        "status_counts": statuses,
        "throughput_rps": round(requests / elapsed, 1),
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(percentile(latencies, 95), 1),
            "p99": round(percentile(latencies, 99), 1),
            "mean": round(statistics.fmean(latencies), 1),
        } if latencies else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8200")
    parser.add_argument("--email", default="analyst@acme.demo")
    parser.add_argument("--password", default=os.environ.get("NIMBRIX_DEMO_PASSWORD", "nimbrix-demo-password"))
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--output")
    args = parser.parse_args()
    token = login(args.base_url, args.email, args.password)
    report = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {"machine": platform.machine(), "system": platform.system(), "python": platform.python_version(), "cpus": os.cpu_count()},
        "target": args.base_url,
        "results": {name: run(args.base_url, token, path, args.requests, args.concurrency) for name, path in ENDPOINTS.items()},
    }
    text = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)  # noqa: T201


if __name__ == "__main__":
    main()
