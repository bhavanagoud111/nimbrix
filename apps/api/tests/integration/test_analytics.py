"""Phase 3: cost analytics precision, budgets, recommendations, and simulation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nimbrix_api.models import BillingImport
from nimbrix_api.modules.ingestion import service as ingestion
from nimbrix_api.modules.ingestion.records import CostRecord
from tests.integration.conftest import DEMO_END, TenantUsers, create_tenant, drain_jobs, idem

ClientFor = Callable[..., TestClient]
DAY = timedelta(days=1)


def _record(
    day: date,
    amount: str,
    *,
    currency: str = "USD",
    team: str | None = "a",
    service: str = "S1",
    quantity: str | None = None,
    unit: str | None = None,
    rid: str | None = None,
) -> CostRecord:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    tags = {"team": team} if team else {}
    return CostRecord(
        source_record_id=rid or f"t:{uuid.uuid4().hex}",
        provider="aws",
        account_id="123456789012",
        service=service,
        usage_start=start,
        usage_end=start + DAY,
        amount=Decimal(amount),
        currency=currency,
        region="us-east-1",
        usage_quantity=Decimal(quantity) if quantity else None,
        usage_unit=unit,
        tags={**tags, "environment": "prod"},
    )


@pytest.fixture
def precise_tenant(db: Session) -> TenantUsers:
    """Hand-built data with known exact sums."""
    users = create_tenant(db, ("viewer", "admin"))
    imp = BillingImport(
        tenant_id=users.tenant_id, source="csv", state="succeeded", errors=[], details={}
    )
    db.add(imp)
    db.flush()
    records = []
    # Sept 1-30: 0.1 + 0.2 per day for team a (S1) -> exactly 0.3/day; team None 1.00/day (S2).
    for i in range(30):
        d = date(2026, 9, 1) + i * DAY
        records += [
            _record(d, "0.1"),
            _record(d, "0.2"),
            _record(d, "1.00", team=None, service="S2", quantity="4", unit="GB"),
        ]
    # Aug 1-31: 0.5/day team a.
    for i in range(31):
        records.append(_record(date(2026, 8, 1) + i * DAY, "0.5"))
    records.append(_record(date(2026, 9, 15), "99.99", currency="EUR"))
    ingestion.upsert_cost_records(db, users.tenant_id, imp.id, records)
    db.commit()
    return users


def test_summary_is_exact_and_never_mixes_currencies(
    client_for: ClientFor, precise_tenant: TenantUsers
) -> None:
    client = client_for(precise_tenant.emails["viewer"])
    s = client.get("/api/v1/costs/summary?start=2026-09-01&end=2026-09-30").json()
    assert s["as_of"] == "2026-09-30"
    assert s["data_mode"] == "live"
    assert Decimal(s["total"]) == Decimal("39.000000")  # (0.3 + 1.0) x 30, no float drift
    assert Decimal(s["allocated"]) == Decimal("9.000000")
    assert Decimal(s["unallocated"]) == Decimal("30.000000")
    assert Decimal(s["allocation_pct"]) == Decimal("23.08")
    assert s["previous_window"] == {"start": "2026-08-02", "end": "2026-08-31"}
    assert Decimal(s["previous_total"]) == Decimal("15.000000")
    assert Decimal(s["change_pct"]) == Decimal("160.00")
    assert s["other_currencies"] == [{"currency": "EUR", "total": "99.990000"}]
    mtd = s["month_to_date"]
    assert (mtd["start"], mtd["end"], mtd["previous_end"]) == (
        "2026-09-01",
        "2026-09-30",
        "2026-08-30",
    )
    assert Decimal(mtd["previous_amount"]) == Decimal("15.000000")
    eur = client.get("/api/v1/costs/summary?start=2026-09-01&end=2026-09-30&currency=EUR").json()
    assert Decimal(eur["total"]) == Decimal("99.990000")


def test_zero_data_and_invalid_windows(client_for: ClientFor, db: Session) -> None:
    empty = create_tenant(db, ("viewer",))
    client = client_for(empty.emails["viewer"])
    assert client.get("/api/v1/costs/summary").json()["has_data"] is False
    assert client.get("/api/v1/costs/timeseries").json()["series"] == []
    assert client.get("/api/v1/budgets").json()["items"] == []
    assert client.get("/api/v1/recommendations").json()["items"] == []
    assert client.get("/api/v1/costs/summary?start=2026-09-02&end=2026-09-01").status_code in (
        200,
        422,
    )


def test_window_validation(client_for: ClientFor, precise_tenant: TenantUsers) -> None:
    client = client_for(precise_tenant.emails["viewer"])
    assert client.get("/api/v1/costs/summary?start=2026-09-02&end=2026-09-01").status_code == 422
    assert (
        client.get("/api/v1/costs/summary?start=2020-01-01&end=2026-09-01").json()["error"]["code"]
        == "window_too_large"
    )


def test_timeseries_fills_gaps_and_groups(
    client_for: ClientFor, precise_tenant: TenantUsers
) -> None:
    client = client_for(precise_tenant.emails["viewer"])
    ts = client.get("/api/v1/costs/timeseries?start=2026-09-28&end=2026-10-02&group_by=team").json()
    assert ts["buckets"] == ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    series = {s["key"]: s for s in ts["series"]}
    assert set(series) == {"a", "(unallocated)"}
    assert [Decimal(p["amount"]) for p in series["a"]["points"]] == [Decimal("0.3")] * 3 + [
        Decimal(0)
    ] * 2
    monthly = client.get(
        "/api/v1/costs/timeseries?start=2026-08-01&end=2026-09-30&granularity=month"
    ).json()
    assert monthly["buckets"] == ["2026-08-01", "2026-09-01"]
    assert [Decimal(p["amount"]) for p in monthly["series"][0]["points"]] == [
        Decimal("15.5"),
        Decimal("39"),
    ]
    top1 = client.get(
        "/api/v1/costs/timeseries?start=2026-09-01&end=2026-09-30&group_by=service&top=1"
    ).json()
    assert [s["key"] for s in top1["series"]] == ["S2", "(other)"]


def test_breakdown_filters_and_unit_costs(
    client_for: ClientFor, precise_tenant: TenantUsers
) -> None:
    client = client_for(precise_tenant.emails["viewer"])
    b = client.get("/api/v1/costs/breakdown?dimension=team&start=2026-09-01&end=2026-09-30").json()
    shares = {i["key"]: Decimal(i["share_pct"]) for i in b["items"]}
    assert shares == {"(unallocated)": Decimal("76.92"), "a": Decimal("23.08")}
    tagged = client.get(
        "/api/v1/costs/summary?start=2026-09-01&end=2026-09-30&tag_key=team&tag_value=a"
    ).json()
    assert Decimal(tagged["total"]) == Decimal("9")
    unallocated = client.get(
        "/api/v1/costs/summary?start=2026-09-01&end=2026-09-30&team=(unallocated)"
    ).json()
    assert Decimal(unallocated["total"]) == Decimal("30")
    units = client.get("/api/v1/costs/unit-costs?start=2026-09-01&end=2026-09-30").json()["items"]
    assert units == [
        {
            "service": "S2",
            "usage_unit": "GB",
            "amount": "30.000000",
            "usage_quantity": "120.00000000",
            "unit_cost": "0.25000000",
        }
    ]
    dims = client.get("/api/v1/costs/dimensions").json()
    assert dims["currencies"]
    assert set(dims["currencies"]) == {"USD", "EUR"}
    assert "(unallocated)" in dims["team"]


def test_budget_utilization_and_alerts(client_for: ClientFor, precise_tenant: TenantUsers) -> None:
    admin = client_for(precise_tenant.emails["admin"])
    created = admin.post(
        "/api/v1/budgets",
        json={
            "name": "Team A",
            "scope_type": "team",
            "scope_value": "a",
            "amount": "10.00",
            "alert_thresholds": ["0.5", "0.8", "1.0"],
        },
        headers=idem(),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert Decimal(body["actual"]) == Decimal("9")  # September MTD, as_of 2026-09-30
    assert Decimal(body["utilization"]) == Decimal("0.9000")
    assert body["projection_method"] == "run_rate"
    assert body["status"] == "warning"
    viewer = client_for(precise_tenant.emails["viewer"])
    assert (
        viewer.post(
            "/api/v1/budgets",
            json={"name": "x", "scope_type": "total", "amount": "1"},
            headers=idem(),
        ).status_code
        == 403
    )
    assert (
        admin.post(
            "/api/v1/budgets",
            json={"name": "bad", "scope_type": "team", "amount": "1"},
            headers=idem(),
        ).status_code
        == 422
    )


def test_demo_recommendations_and_simulation(
    client_for: ClientFor, demo_tenant: TenantUsers
) -> None:
    analyst = client_for(demo_tenant.emails["analyst"])
    recs = analyst.get("/api/v1/recommendations?limit=100").json()
    by_title = {r["title"]: r for r in recs["items"]}
    idle = by_title["Stop idle non-production instance dev-build-runner-legacy"]
    assert Decimal(idle["estimated_monthly_savings"]) == Decimal("280.32")
    assert idle["status"] == "open"
    assert idle["owner"] == "platform"
    assert idle["proposed_action"] == "simulate_stop_nonprod_instance"
    assert {c["condition"] for c in idle["eligibility"]} >= {
        "cpu_avg_pct",
        "cpu_p95_pct",
        "datapoint_coverage",
    }
    categories = {r["category"] for r in recs["items"]}
    assert categories == {
        "idle_nonprod_instance",
        "underutilized_compute",
        "unattached_volume",
        "unused_elastic_ip",
        "nonprod_scheduling",
    }
    canary = [r for r in recs["items"] if r["status"] == "insufficient_evidence"]
    assert len(canary) == 1
    assert canary[0]["estimated_monthly_savings"] is None
    assert not any("dev-vpn-gateway" in r["title"] for r in recs["items"])  # protected
    assert Decimal(recs["total_estimated_monthly_savings"]) > Decimal("500")

    sim = analyst.post(f"/api/v1/recommendations/{idle['id']}/simulate").json()
    assert sim["mode"] == "simulation"
    assert sim["realized"] is False
    assert Decimal(sim["current_monthly_cost"]) == Decimal("280.32")
    assert Decimal(sim["projected_monthly_cost"]) == Decimal("0.00")
    assert (
        Decimal(sim["savings_range"]["low"])
        <= Decimal("280.32")
        <= Decimal(sim["savings_range"]["high"])
    )
    assert sim["basis"]["observed_billed_monthly_run_rate"] is not None
    assert any(
        "EBS volume" in e["item"] and e["monthly_amount"] == "8.00" for e in sim["excluded_costs"]
    )
    assert (
        analyst.get(f"/api/v1/recommendations/{idle['id']}").json()["last_simulation"]["mode"]
        == "simulation"
    )
    insufficient = analyst.post(f"/api/v1/recommendations/{canary[0]['id']}/simulate").json()
    assert insufficient["status"] == "insufficient_evidence"

    viewer = client_for(demo_tenant.emails["viewer"])
    assert viewer.post(f"/api/v1/recommendations/{idle['id']}/simulate").status_code == 403


def test_refresh_preserves_user_decisions(client_for: ClientFor, demo_tenant: TenantUsers) -> None:
    analyst = client_for(demo_tenant.emails["analyst"])
    recs = analyst.get("/api/v1/recommendations?category=unused_elastic_ip").json()["items"]
    eip = recs[0]
    dismissed = analyst.post(
        f"/api/v1/recommendations/{eip['id']}/dismiss", json={"reason": "kept for DR failover"}
    )
    assert dismissed.json()["status"] == "dismissed"
    job = analyst.post("/api/v1/analysis/run").json()
    drain_jobs()
    assert analyst.get(f"/api/v1/jobs/{job['id']}").json()["state"] == "succeeded"
    assert analyst.get(f"/api/v1/recommendations/{eip['id']}").json()["status"] == "dismissed"


def test_demo_budgets_show_spike_pressure(client_for: ClientFor, demo_tenant: TenantUsers) -> None:
    viewer = client_for(demo_tenant.emails["viewer"])
    budgets = {b["name"]: b for b in viewer.get("/api/v1/budgets").json()["items"]}
    assert set(budgets) == {"Total cloud spend", "Search team", "Development environment"}
    search = budgets["Search team"]
    assert search["as_of"] == str(DEMO_END)
    assert search["status"] in ("at_risk", "over_budget")
    alerts = viewer.get("/api/v1/budgets/alerts").json()["items"]
    assert any(a["budget_name"] == "Search team" for a in alerts)
