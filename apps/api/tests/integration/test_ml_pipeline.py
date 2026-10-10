"""Phase 4 against PostgreSQL: anomalies on the demo spike, forecasts, lifecycle, scheduling."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nimbrix_api.models import Job
from nimbrix_api.modules.analysis.scheduler import schedule_daily_analysis
from nimbrix_api.modules.ingestion import synthetic
from tests.integration.conftest import (
    DEMO_END,
    DRAIN,
    TenantUsers,
    create_tenant,
    drain_jobs,
    load_demo,
)

ClientFor = Callable[..., TestClient]
SPIKE_START = DEMO_END - timedelta(days=synthetic.SPIKE_LAUNCH_DAYS_BEFORE_END)


def test_demo_spike_is_detected_with_evidence(
    client_for: ClientFor, demo_tenant: TenantUsers
) -> None:
    viewer = client_for(demo_tenant.emails["viewer"])
    anomalies = viewer.get("/api/v1/anomalies?limit=100").json()["items"]
    by_scope = {(a["scope_type"], a["scope_value"]): a for a in anomalies}
    for scope in (
        ("service", "AmazonEC2"),
        ("account", "333333333333"),
        ("team", "search"),
        ("total", "total"),
    ):
        assert scope in by_scope, f"missing anomaly for {scope}"
        assert by_scope[scope]["window_start"] == str(SPIKE_START)
        assert by_scope[scope]["window_end"] == str(DEMO_END)
    ec2 = by_scope[("service", "AmazonEC2")]
    assert ec2["severity"] == "high"
    assert ec2["status"] == "open"
    assert ec2["detector_version"] == "seasonal-residual-v1"
    assert float(ec2["observed"]) > float(ec2["expected"])
    contributors = ec2["evidence"]["contributors"]
    assert len(contributors) == 8
    assert all(c["new_in_window"] for c in contributors)
    assert all(c["evidence_id"].startswith("resource:i-") for c in contributors)
    assert {d["date"] for d in ec2["evidence"]["daily"]} == {
        str(SPIKE_START + timedelta(days=i))
        for i in range(synthetic.SPIKE_LAUNCH_DAYS_BEFORE_END + 1)
    }
    # No anomalies outside the labeled incident.
    assert all(a["window_start"] == str(SPIKE_START) for a in anomalies)


def test_anomaly_lifecycle(client_for: ClientFor, demo_tenant: TenantUsers) -> None:
    analyst = client_for(demo_tenant.emails["analyst"])
    viewer = client_for(demo_tenant.emails["viewer"])
    target = analyst.get("/api/v1/anomalies?scope_type=account").json()["items"][0]
    url = f"/api/v1/anomalies/{target['id']}/status"
    assert viewer.post(url, json={"status": "acknowledged"}).status_code == 403
    assert (
        analyst.post(url, json={"status": "acknowledged", "note": "looking"}).json()["status"]
        == "acknowledged"
    )
    assert analyst.post(url, json={"status": "resolved"}).json()["status"] == "resolved"
    bad = analyst.post(url, json={"status": "acknowledged"})
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "invalid_transition"
    # A re-run updates evidence but keeps the human decision.
    analyst.post("/api/v1/analysis/run")
    drain_jobs()
    assert analyst.get(f"/api/v1/anomalies/{target['id']}").json()["status"] == "resolved"


def test_forecasts_are_validated_and_reused(
    client_for: ClientFor, demo_tenant: TenantUsers, db: Session
) -> None:
    analyst = client_for(demo_tenant.emails["analyst"])
    forecasts = analyst.get("/api/v1/forecasts?scope_type=total").json()["items"]
    total = forecasts[0]
    assert total["status"] == "succeeded"
    assert total["horizon_days"] == 30
    assert total["training_end"] == str(DEMO_END)
    assert len(total["points"]) == 30
    assert total["points"][0]["date"] == str(DEMO_END + timedelta(days=1))
    assert all(float(p["lower"]) <= float(p["yhat"]) <= float(p["upper"]) for p in total["points"])
    metrics = total["metrics"]
    assert set(metrics["models"]) == {"seasonal_naive", "holt_winters"}
    assert metrics["models"]["seasonal_naive"]["wape"] is not None
    assert metrics["selected_model"] in ("seasonal_naive", "holt_winters")
    assert total["model_version"]

    before = (
        db.scalar(
            select(func.count()).select_from(Job).where(Job.tenant_id == demo_tenant.tenant_id)
        )
        or 0
    )
    job = analyst.post("/api/v1/analysis/run").json()
    drain_jobs()
    result = analyst.get(f"/api/v1/jobs/{job['id']}").json()["result"]
    assert result["forecasts"]["computed"] == 0  # same as_of: reused, not recomputed
    assert result["forecasts"]["reused"] >= 1
    assert (
        db.scalar(
            select(func.count()).select_from(Job).where(Job.tenant_id == demo_tenant.tenant_id)
        )
        == before + 1
    )

    run = analyst.post(
        "/api/v1/forecasts/run",
        json={"horizon_days": 7, "scope_type": "service", "scope_value": "AmazonS3"},
    )
    assert run.status_code == 202
    drain_jobs()
    s3 = analyst.get(
        "/api/v1/forecasts?scope_type=service&scope_value=AmazonS3&horizon_days=7"
    ).json()["items"][0]
    assert len(s3["points"]) == 7
    budgets = {b["name"]: b for b in analyst.get("/api/v1/budgets").json()["items"]}
    assert budgets["Total cloud spend"]["projection_method"] == "forecast"


def test_insufficient_history_is_reported_honestly(client_for: ClientFor, db: Session) -> None:
    users = create_tenant(db, ("analyst",))
    analyst = client_for(users.emails["analyst"])
    assert analyst.post("/api/v1/forecasts/run", json={"horizon_days": 7}).status_code == 202
    drain_jobs()
    forecast = analyst.get("/api/v1/forecasts").json()["items"][0]
    assert forecast["status"] == "insufficient_data"
    assert forecast["points"] == []
    assert "no cost data" in forecast["message"]


def test_daily_scheduler_does_not_duplicate(db: Session) -> None:
    now = datetime.now(UTC)
    first = schedule_daily_analysis(db, now)
    second = schedule_daily_analysis(db, now)
    assert second == 0
    assert first >= 0
    drain_jobs()
    assert schedule_daily_analysis(db, now) == 0
    assert schedule_daily_analysis(db, now + timedelta(days=1)) >= 1
    drain_jobs()


def test_short_history_demo_tenant(db: Session, client_for: ClientFor) -> None:
    users = create_tenant(db, ("analyst",))
    load_demo(DRAIN["factory"], DRAIN["settings"], users.tenant_id, DEMO_END, days=30)
    analyst = client_for(users.emails["analyst"])
    forecast = analyst.get("/api/v1/forecasts?scope_type=total").json()["items"][0]
    assert forecast["status"] == "succeeded"
    assert forecast["metrics"]["models"] == {}  # too short to backtest
    assert "unverified" in forecast["metrics"]["interval_method"]
