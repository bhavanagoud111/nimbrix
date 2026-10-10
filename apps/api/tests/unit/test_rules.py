"""Recommendation rules: eligibility edge cases, insufficient evidence, exact savings math."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from nimbrix_api.models import ResourceInventory
from nimbrix_api.modules.recommendations import rules

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _util(
    avg: float, p95: float, *, datapoints: int = 336, window: int = 14, ratio: float | None = None
) -> dict[str, Any]:
    return {
        "statistic_avg_pct": avg,
        "statistic_p95_pct": p95,
        "datapoints": datapoints,
        "expected_datapoints": 336,
        "window_days": window,
        "period_seconds": 3600,
        "business_hours_ratio": ratio,
    }


def _instance(env: str = "dev", **overrides: Any) -> ResourceInventory:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "provider": "aws",
        "account_id": "333333333333",
        "region": "us-east-1",
        "resource_id": "i-test",
        "resource_type": "ec2_instance",
        "name": "test-box",
        "state": "running",
        "tags": {"environment": env, "team": "platform"},
        "environment": env,
        "team": "platform",
        "attributes": {"instance_type": "m5.2xlarge"},
        "hourly_cost": Decimal("0.384"),
        "monthly_cost": None,
        "currency": "USD",
        "utilization": _util(1.0, 2.0),
        "observed_at": NOW,
        "source": "demo",
    }
    values.update(overrides)
    return ResourceInventory(**values)


def _categories(r: ResourceInventory) -> dict[str, rules.Finding]:
    return {f.category: f for f in rules.evaluate_resource(r, NOW)}


def test_idle_nonprod_instance_savings_are_exact() -> None:
    finding = _categories(_instance())["idle_nonprod_instance"]
    assert finding.estimated_monthly_savings == Decimal("280.32")  # 0.384 x 730
    assert finding.confidence == "high"
    assert finding.risk == "low"
    assert finding.proposed_action == "simulate_stop_nonprod_instance"
    assert all(c["passed"] for c in finding.eligibility)
    assert any("EBS storage keeps billing" in a for a in finding.assumptions)


@pytest.mark.parametrize(
    ("overrides", "expected_failed"),
    [
        ({"utilization": None}, "cpu_metrics_present"),
        ({"utilization": _util(1.0, 2.0, datapoints=100)}, "datapoint_coverage"),
        ({"utilization": _util(1.0, 2.0, window=7)}, "window_days"),
    ],
)
def test_incomplete_metrics_yield_insufficient_evidence(
    overrides: dict[str, Any], expected_failed: str
) -> None:
    finding = _categories(_instance(**overrides))["idle_nonprod_instance"]
    assert finding.insufficient
    assert finding.confidence == "insufficient_evidence"
    assert finding.estimated_monthly_savings is None
    assert expected_failed in {c["condition"] for c in finding.eligibility if not c["passed"]}


def test_missing_price_yields_insufficient_evidence() -> None:
    finding = _categories(_instance(hourly_cost=None))["idle_nonprod_instance"]
    assert finding.insufficient
    assert finding.estimated_monthly_savings is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "prod", "tags": {"environment": "prod"}},
        {"state": "stopped"},
        {"tags": {"environment": "dev", "nimbrix:protected": "true"}},
        {"utilization": _util(30.0, 70.0)},
    ],
)
def test_ineligible_instances_are_not_idle_candidates(overrides: dict[str, Any]) -> None:
    assert "idle_nonprod_instance" not in _categories(_instance(**overrides))


def test_underutilized_prod_is_high_risk_rightsizing() -> None:
    r = _instance("prod", tags={"environment": "prod"}, utilization=_util(9.0, 22.0))
    finding = _categories(r)["underutilized_compute"]
    assert finding.estimated_monthly_savings == Decimal("140.16")  # (0.384 - 0.192) x 730
    assert finding.risk == "high"
    assert finding.proposed_action is None  # rightsizing is never automated


def test_underutilized_requires_a_smaller_size() -> None:
    r = _instance(
        "prod",
        tags={"environment": "prod"},
        utilization=_util(9.0, 22.0),
        attributes={"instance_type": "t3.medium"},
        hourly_cost=Decimal("0.0416"),
    )
    assert "underutilized_compute" not in _categories(r)


def test_nonprod_scheduling_requires_hourly_pattern_evidence() -> None:
    with_ratio = _instance(
        attributes={"instance_type": "r5.large"},
        hourly_cost=Decimal("0.126"),
        utilization=_util(14.0, 48.0, ratio=0.93),
    )
    finding = _categories(with_ratio)["nonprod_scheduling"]
    expected = (Decimal("0.126") * 730 * Decimal(108) / Decimal(168)).quantize(Decimal("0.01"))
    assert finding.estimated_monthly_savings == expected == Decimal("59.13")
    without_ratio = _instance(utilization=_util(14.0, 48.0, ratio=None))
    assert "nonprod_scheduling" not in _categories(without_ratio)


def test_unattached_volume_age_and_price() -> None:
    old = _instance(
        resource_type="ebs_volume",
        state="available",
        hourly_cost=None,
        monthly_cost=Decimal("40.000000"),
        utilization=None,
        attributes={
            "size_gb": 500,
            "volume_type": "gp3",
            "detached_at": (NOW - timedelta(days=45)).isoformat(),
        },
    )
    finding = _categories(old)["unattached_volume"]
    assert finding.estimated_monthly_savings == Decimal("40.00")
    assert finding.confidence == "high"
    fresh = _instance(
        resource_type="ebs_volume",
        state="available",
        monthly_cost=Decimal(40),
        utilization=None,
        attributes={"detached_at": (NOW - timedelta(days=2)).isoformat()},
    )
    assert "unattached_volume" not in _categories(fresh)
    unknown_price = _instance(
        resource_type="ebs_volume",
        state="available",
        monthly_cost=None,
        utilization=None,
        attributes={"volume_type": "io2", "size_gb": 10},
    )
    assert _categories(unknown_price)["unattached_volume"].insufficient


def test_unused_elastic_ip() -> None:
    eip = _instance(
        resource_type="elastic_ip",
        state="unassociated",
        hourly_cost=Decimal("0.005"),
        utilization=None,
    )
    assert _categories(eip)["unused_elastic_ip"].estimated_monthly_savings == Decimal("3.65")
    associated = _instance(resource_type="elastic_ip", state="associated", utilization=None)
    assert _categories(associated) == {}
