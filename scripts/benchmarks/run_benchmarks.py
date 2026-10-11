"""End-to-end benchmarks against a running demo stack (make demo).

    cd apps/api && uv run python ../../scripts/benchmarks/run_benchmarks.py --output ../../docs/results/benchmarks.json

Measures, over HTTP through the web origin (the path a browser takes):
  1. ingestion throughput: a 365-day synthetic import and a 60,000-row CSV upload (worker time);
  2. cost-query latency: tests/performance/load_cost_queries.py against the API;
  3. forecast run time (job duration) for 7- and 30-day horizons;
  4. the simulated workflow: investigate -> request -> approve -> execute -> verified.
All timings are wall-clock on the host running the stack.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import platform
import random
import subprocess
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = os.environ.get("NIMBRIX_DEMO_PASSWORD", "nimbrix-demo-password")


class Client:
    def __init__(self, base: str, email: str) -> None:
        self.http = httpx2.Client(base_url=base, timeout=120)
        response = self.http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        response.raise_for_status()
        self.csrf = response.json()["csrf_token"]

    def get(self, path: str, **kw: Any) -> Any:
        r = self.http.get(path, **kw)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, **kw: Any) -> Any:
        headers = {"X-CSRF-Token": self.csrf, "Idempotency-Key": f"bench-{uuid.uuid4().hex}", **kw.pop("headers", {})}
        r = self.http.post(path, headers=headers, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def wait(self, path: str, done: set[str], key: str = "state", timeout: float = 600) -> tuple[Any, float]:
        started = time.perf_counter()
        while True:
            body = self.get(path)
            if body[key] in done:
                return body, time.perf_counter() - started
            if time.perf_counter() - started > timeout:
                raise TimeoutError(path)
            time.sleep(0.25)


def seconds_between(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


def ingestion(admin: Client) -> dict[str, Any]:
    results: dict[str, Any] = {}
    end = date.today() - timedelta(days=1)
    seed = random.randint(1000, 9_999_999)  # noqa: S311 - unique dataset per run
    created = admin.post("/api/v1/imports/demo", json={"end_date": str(end), "days": 365, "seed": seed})
    done, _ = admin.wait(f"/api/v1/imports/{created['id']}", {"succeeded", "failed", "partially_failed"})
    worker_s = seconds_between(done["started_at"], done["finished_at"])
    results["synthetic_365d"] = {
        "rows": done["rows_received"], "resources": done["resources_upserted"], "state": done["state"],
        "worker_seconds": round(worker_s, 2), "rows_per_second": round(done["rows_received"] / worker_s, 0),
    }

    rows, buffer = 60_000, io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["line_item_id", "usage_start", "usage_end", "account_id", "service", "region", "resource_id",
                     "usage_type", "usage_quantity", "usage_unit", "amount", "currency", "tag:team", "tag:environment"])
    rng = random.Random(seed)  # noqa: S311
    run = uuid.uuid4().hex[:8]
    for i in range(rows):
        day = end - timedelta(days=i % 180)
        writer.writerow([f"bench-{run}-{i}", f"{day}T00:00:00Z", f"{day + timedelta(days=1)}T00:00:00Z",
                         f"{444400000000 + i % 20:012d}", rng.choice(["AmazonEC2", "AmazonS3", "AmazonRDS", "AWSLambda"]),
                         rng.choice(["us-east-1", "us-west-2", "eu-west-1"]), f"i-0bench{i % 2000:08d}", "BoxUsage",
                         "24", "Hrs", f"{rng.uniform(0.1, 20):.6f}", "USD", rng.choice(["a", "b", "c"]), "prod"])
    payload = buffer.getvalue().encode()
    created = admin.post("/api/v1/imports/upload", files={"file": ("bench.csv", payload, "text/csv")})
    done, _ = admin.wait(f"/api/v1/imports/{created['id']}", {"succeeded", "failed", "partially_failed"})
    worker_s = seconds_between(done["started_at"], done["finished_at"])
    results["csv_upload"] = {
        "rows": done["rows_received"], "bytes": len(payload), "state": done["state"],
        "worker_seconds": round(worker_s, 2), "rows_per_second": round(done["rows_received"] / worker_s, 0),
    }
    return results


def forecasts(analyst: Client) -> dict[str, Any]:
    out = {}
    for horizon in (7, 30):
        job = analyst.post("/api/v1/forecasts/run", json={"scope_type": "total", "scope_value": "total",
                                                           "horizon_days": horizon, "currency": "USD", "interval_level": 0.8})
        done, _ = analyst.wait(f"/api/v1/jobs/{job['id']}", {"succeeded", "failed"})
        forecast = analyst.get("/api/v1/forecasts", params={"scope_type": "total", "horizon_days": horizon})["items"][0]
        out[f"{horizon}d"] = {
            "job_seconds": round(seconds_between(done["started_at"], done["finished_at"]), 2),
            "selected_model": forecast["metrics"].get("selected_model"),
            "backtest": forecast["metrics"].get("models"),
        }
    return out


def workflow(analyst: Client, approver: Client) -> dict[str, Any]:
    anomaly = analyst.get("/api/v1/anomalies", params={"scope_type": "service"})["items"][0]
    t0 = time.perf_counter()
    inv = analyst.post(f"/api/v1/anomalies/{anomaly['id']}/investigate", json={})
    inv, _ = analyst.wait(f"/api/v1/investigations/{inv['id']}", {"completed", "failed", "cancelled"})
    t_investigation = time.perf_counter() - t0
    resource = analyst.get("/api/v1/resources", params={"q": "search-dev", "resource_type": "ec2_instance"})["items"][0]
    t1 = time.perf_counter()
    approval = analyst.post("/api/v1/approvals", json={"action_type": "simulate_stop_nonprod_instance", "payload": {
        "resource_id": resource["resource_id"], "account_id": resource["account_id"], "region": resource["region"],
        "reason": "benchmark run"}})
    approver.post(f"/api/v1/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]})
    execution = approver.post("/api/v1/remediations", json={"approval_request_id": approval["id"]})
    execution, _ = approver.wait(f"/api/v1/remediations/{execution['id']}", {"succeeded", "failed"})
    t_control = time.perf_counter() - t1
    return {
        "investigation_seconds": round(t_investigation, 2),
        "investigation_state": inv["state"],
        "investigation_steps": len(inv["steps"]),
        "request_approve_execute_verify_seconds": round(t_control, 2),
        "execution_state": execution["state"],
        "verified": execution["verification"].get("verified"),
        "total_seconds": round(t_investigation + t_control, 2),
    }


def query_latency(api_base: str, requests: int, concurrency: int) -> dict[str, Any]:
    out = subprocess.run(  # noqa: S603 - fixed local script
        [sys.executable, str(ROOT / "tests" / "performance" / "load_cost_queries.py"), "--base-url", api_base,
         "--requests", str(requests), "--concurrency", str(concurrency)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)["results"]


def docker_resources() -> dict[str, Any]:
    try:
        info = json.loads(subprocess.run(["docker", "info", "--format", "{{json .}}"], check=True,  # noqa: S607
                                         capture_output=True, text=True).stdout)
        return {"docker_cpus": info.get("NCPU"), "docker_memory_gb": round(info.get("MemTotal", 0) / 1024**3, 1),
                "docker_version": info.get("ServerVersion")}
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--web", default="http://127.0.0.1:3200")
    parser.add_argument("--api", default="http://127.0.0.1:8200")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--output")
    args = parser.parse_args()
    analyst = Client(args.web, "analyst@acme.demo")
    approver = Client(args.web, "approver@acme.demo")
    globex = Client(args.web, "admin@globex.demo")
    report = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": {"machine": platform.machine(), "system": f"{platform.system()} {platform.release()}",
                 "cpus": os.cpu_count(), **docker_resources()},
        "workflow": workflow(analyst, approver),
        "forecasts": forecasts(analyst),
        "query_latency_acme_120d": query_latency(args.api, args.requests, args.concurrency),
        "ingestion_globex": ingestion(globex),
    }
    text = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n")
    print(text)  # noqa: T201


if __name__ == "__main__":
    main()
