"""Transparent, versioned optimization rules.

Each rule inspects one inventory item and returns either nothing (not a candidate), a
recommendation, or an ``insufficient_evidence`` finding when the signal it needs is
missing. Rules never guess: an unknown price or missing utilization data produces
``insufficient_evidence`` with no savings figure. All money is ``Decimal``.

Savings are **estimates** of monthly list-price reductions; they are never realized savings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from nimbrix_api.models import ResourceInventory
from nimbrix_api.modules.ingestion.records import NONPROD_ENVIRONMENTS
from nimbrix_api.modules.pricing import (
    EC2_DOWNSIZE,
    EC2_HOURLY_USD,
    HOURS_PER_MONTH,
    PRICE_SOURCE,
    PUBLIC_IPV4_HOURLY_USD,
)

RULE_VERSION = "rules-v1"
CENTS = Decimal("0.01")
PROTECTED_TAG = "nimbrix:protected"

IDLE_CPU_AVG_MAX = 5.0
IDLE_CPU_P95_MAX = 10.0
UNDERUTILIZED_CPU_AVG_MAX = 20.0
UNDERUTILIZED_CPU_P95_MAX = 40.0
MIN_WINDOW_DAYS = 14
MIN_COVERAGE = 0.9
SCHEDULE_BUSINESS_RATIO_MIN = 0.8
SCHEDULED_HOURS_PER_WEEK = Decimal(60)  # 12 h x 5 weekdays
HOURS_PER_WEEK = Decimal(168)
UNATTACHED_MIN_DAYS = 7


@dataclass
class Finding:
    category: str
    title: str
    summary: str
    resource: ResourceInventory
    eligibility: list[dict[str, Any]]
    evidence: dict[str, Any]
    assumptions: list[str]
    confidence: str
    risk: str
    savings_formula: str | None = None
    estimated_monthly_savings: Decimal | None = None
    proposed_action: str | None = None
    utilization_window: dict[str, Any] | None = None
    insufficient: bool = False
    extra_resources: list[str] = field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        return f"{self.category}:{self.resource.provider}:{self.resource.resource_id}"


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _check(name: str, passed: bool, observed: Any, required: str) -> dict[str, Any]:
    return {"condition": name, "passed": passed, "observed": observed, "required": required}


def _resource_evidence(r: ResourceInventory) -> dict[str, Any]:
    return {
        "resource_inventory_id": str(r.id),
        "resource_id": r.resource_id,
        "resource_type": r.resource_type,
        "name": r.name,
        "account_id": r.account_id,
        "region": r.region,
        "state": r.state,
        "environment": r.environment,
        "team": r.team,
        "observed_at": r.observed_at.isoformat(),
        "source": r.source,
    }


def _utilization_checks(util: dict[str, Any] | None) -> tuple[bool, list[dict[str, Any]], float]:
    """Is the CPU evidence complete enough to judge? Returns (sufficient, checks, coverage)."""
    if not util or util.get("statistic_avg_pct") is None or util.get("statistic_p95_pct") is None:
        return (
            False,
            [_check("cpu_metrics_present", False, None, "CloudWatch CPU avg and p95")],
            0.0,
        )
    expected = util.get("expected_datapoints") or 0
    coverage = (util.get("datapoints") or 0) / expected if expected else 0.0
    window = util.get("window_days") or 0
    checks = [
        _check("cpu_metrics_present", True, "avg+p95", "CloudWatch CPU avg and p95"),
        _check("window_days", window >= MIN_WINDOW_DAYS, window, f">= {MIN_WINDOW_DAYS}"),
        _check(
            "datapoint_coverage", coverage >= MIN_COVERAGE, round(coverage, 3), f">= {MIN_COVERAGE}"
        ),
    ]
    return all(c["passed"] for c in checks), checks, coverage


def _is_protected(r: ResourceInventory) -> bool:
    return str(r.tags.get(PROTECTED_TAG, "")).lower() == "true"


def idle_nonprod_instance(r: ResourceInventory) -> Finding | None:
    if (
        r.resource_type != "ec2_instance"
        or r.state != "running"
        or r.environment not in NONPROD_ENVIRONMENTS
    ):
        return None
    if _is_protected(r):
        return None
    util = r.utilization
    sufficient, checks, coverage = _utilization_checks(util)
    base = _resource_evidence(r)
    if not sufficient:
        return Finding(
            category="idle_nonprod_instance",
            title=f"Cannot assess idleness of {r.name or r.resource_id}",
            summary="Utilization evidence is missing or incomplete, so idleness cannot be judged. "
            "No savings are estimated.",
            resource=r,
            eligibility=checks,
            evidence={"resource": base, "utilization": util},
            assumptions=[
                "A recommendation requires at least 14 days of hourly CPU data with 90% coverage."
            ],
            confidence="insufficient_evidence",
            risk="low",
            insufficient=True,
        )
    assert util is not None  # noqa: S101
    avg, p95 = float(util["statistic_avg_pct"]), float(util["statistic_p95_pct"])
    checks += [
        _check("environment_nonprod", True, r.environment, "one of staging/dev/test/sandbox"),
        _check("not_protected", True, r.tags.get(PROTECTED_TAG), f"{PROTECTED_TAG} != true"),
        _check("cpu_avg_pct", avg < IDLE_CPU_AVG_MAX, avg, f"< {IDLE_CPU_AVG_MAX}"),
        _check("cpu_p95_pct", p95 < IDLE_CPU_P95_MAX, p95, f"< {IDLE_CPU_P95_MAX}"),
    ]
    if not all(c["passed"] for c in checks):
        return None
    hourly = r.hourly_cost
    price_check = _check(
        "hourly_price_known", hourly is not None, str(hourly) if hourly else None, "list price"
    )
    if hourly is None:
        return Finding(
            category="idle_nonprod_instance",
            title=f"Idle non-production instance {r.name or r.resource_id} (price unknown)",
            summary="The instance looks idle but its price is unknown, so savings are not "
            "estimated.",
            resource=r,
            eligibility=[*checks, price_check],
            evidence={"resource": base, "utilization": util},
            assumptions=["No list price is available for this instance type."],
            confidence="insufficient_evidence",
            risk="low",
            insufficient=True,
        )
    savings = _money(hourly * HOURS_PER_MONTH)
    root_volume = r.attributes.get("root_volume_id")
    return Finding(
        category="idle_nonprod_instance",
        title=f"Stop idle non-production instance {r.name or r.resource_id}",
        summary=f"{r.environment} instance averaged {avg:.1f}% CPU (p95 {p95:.1f}%) over "
        f"{util['window_days']} days while running 24/7.",
        resource=r,
        eligibility=[*checks, price_check],
        evidence={"resource": base, "utilization": util, "hourly_price": str(hourly)},
        assumptions=[
            f"Price: {PRICE_SOURCE}.",
            "Stopping removes instance-hour charges only; attached EBS storage keeps billing.",
            "730 hours per month.",
            "CPU is the only utilization signal; memory and disk I/O were not observed.",
        ],
        confidence="high" if coverage >= 0.95 and avg < 2 else "medium",
        risk="low",
        savings_formula="hourly_price x 730 h",
        estimated_monthly_savings=savings,
        proposed_action="simulate_stop_nonprod_instance",
        utilization_window={
            "days": util["window_days"],
            "period_seconds": util.get("period_seconds"),
        },
        extra_resources=[root_volume] if root_volume else [],
    )


def underutilized_compute(r: ResourceInventory) -> Finding | None:
    if r.resource_type != "ec2_instance" or r.state != "running":
        return None
    sufficient, checks, _ = _utilization_checks(r.utilization)
    if not sufficient or r.utilization is None:
        return None  # idle rule already reports missing evidence for non-prod
    avg, p95 = float(r.utilization["statistic_avg_pct"]), float(r.utilization["statistic_p95_pct"])
    if r.environment in NONPROD_ENVIRONMENTS and avg < IDLE_CPU_AVG_MAX and p95 < IDLE_CPU_P95_MAX:
        return None  # handled by the idle rule
    instance_type = r.attributes.get("instance_type")
    smaller = EC2_DOWNSIZE.get(instance_type or "")
    checks += [
        _check(
            "cpu_avg_pct", avg < UNDERUTILIZED_CPU_AVG_MAX, avg, f"< {UNDERUTILIZED_CPU_AVG_MAX}"
        ),
        _check(
            "cpu_p95_pct", p95 < UNDERUTILIZED_CPU_P95_MAX, p95, f"< {UNDERUTILIZED_CPU_P95_MAX}"
        ),
        _check(
            "smaller_size_exists", smaller is not None, smaller, "one size down in the same family"
        ),
    ]
    if not all(c["passed"] for c in checks) or smaller is None:
        return None
    current = EC2_HOURLY_USD.get(instance_type or "")
    target = EC2_HOURLY_USD.get(smaller)
    if current is None or target is None:
        return None
    savings = _money((current - target) * HOURS_PER_MONTH)
    prod = r.environment not in NONPROD_ENVIRONMENTS
    return Finding(
        category="underutilized_compute",
        title=f"Rightsize {r.name or r.resource_id} from {instance_type} to {smaller}",
        summary=f"Averaged {avg:.1f}% CPU (p95 {p95:.1f}%) over "
        f"{r.utilization['window_days']} days; "
        f"one size down halves vCPU and memory.",
        resource=r,
        eligibility=checks,
        evidence={
            "resource": _resource_evidence(r),
            "utilization": r.utilization,
            "current_hourly_price": str(current),
            "target_hourly_price": str(target),
        },
        assumptions=[
            f"Price: {PRICE_SOURCE}.",
            "One size down has half the vCPU/memory; peak CPU would roughly double.",
            "Memory utilization was not observed; validate before resizing.",
            "Resizing requires a stop/start and is not automated by Nimbrix.",
        ],
        confidence="medium",
        risk="high" if prod else "medium",
        savings_formula="(current_hourly - target_hourly) x 730 h",
        estimated_monthly_savings=savings,
        utilization_window={
            "days": r.utilization["window_days"],
            "period_seconds": r.utilization.get("period_seconds"),
        },
    )


def unattached_volume(r: ResourceInventory, now: datetime) -> Finding | None:
    if r.resource_type != "ebs_volume" or r.state != "available":
        return None
    detached_at = r.attributes.get("detached_at")
    days = (now - datetime.fromisoformat(detached_at)).days if detached_at else None
    checks = [
        _check("state_available", True, r.state, "available (not attached)"),
        _check(
            "unattached_days",
            days is None or days >= UNATTACHED_MIN_DAYS,
            days,
            f">= {UNATTACHED_MIN_DAYS} (if known)",
        ),
    ]
    if days is not None and days < UNATTACHED_MIN_DAYS:
        return None
    if r.monthly_cost is None:
        return Finding(
            category="unattached_volume",
            title=f"Unattached volume {r.name or r.resource_id} (price unknown)",
            summary="The volume is not attached, but its storage price is unknown.",
            resource=r,
            eligibility=[*checks, _check("price_known", False, None, "volume type + size price")],
            evidence={"resource": _resource_evidence(r), "attributes": r.attributes},
            assumptions=["No list price for this volume type/size."],
            confidence="insufficient_evidence",
            risk="medium",
            insufficient=True,
        )
    return Finding(
        category="unattached_volume",
        title=f"Delete or snapshot unattached volume {r.name or r.resource_id}",
        summary=f"{r.attributes.get('size_gb')} GB {r.attributes.get('volume_type')} volume "
        "has been "
        f"unattached for {days if days is not None else 'an unknown number of'} days.",
        resource=r,
        eligibility=checks,
        evidence={
            "resource": _resource_evidence(r),
            "attributes": r.attributes,
            "monthly_price": str(r.monthly_cost),
        },
        assumptions=[
            f"Price: {PRICE_SOURCE}.",
            "Deleting is irreversible; take a snapshot first if the data may be needed "
            "(snapshot storage is billed separately and is not included).",
        ],
        confidence="high" if days is not None else "medium",
        risk="medium",
        savings_formula="size_gb x price_per_gb_month",
        estimated_monthly_savings=_money(r.monthly_cost),
    )


def unused_elastic_ip(r: ResourceInventory) -> Finding | None:
    if r.resource_type != "elastic_ip" or r.state != "unassociated":
        return None
    hourly = r.hourly_cost or PUBLIC_IPV4_HOURLY_USD
    return Finding(
        category="unused_elastic_ip",
        title=f"Release unused Elastic IP {r.name or r.resource_id}",
        summary="The public IPv4 address is allocated but not associated with any resource.",
        resource=r,
        eligibility=[_check("association", True, None, "no association")],
        evidence={"resource": _resource_evidence(r), "attributes": r.attributes},
        assumptions=[
            f"Public IPv4 is billed at {hourly} USD/hour whether or not it is used "
            f"({PRICE_SOURCE})."
        ],
        confidence="high",
        risk="low",
        savings_formula="ipv4_hourly_price x 730 h",
        estimated_monthly_savings=_money(hourly * HOURS_PER_MONTH),
    )


def nonprod_scheduling(r: ResourceInventory) -> Finding | None:
    if (
        r.resource_type != "ec2_instance"
        or r.state != "running"
        or r.environment not in NONPROD_ENVIRONMENTS
    ):
        return None
    if _is_protected(r) or r.hourly_cost is None:
        return None
    sufficient, checks, _ = _utilization_checks(r.utilization)
    util = r.utilization
    if not sufficient or util is None:
        return None
    avg, p95 = float(util["statistic_avg_pct"]), float(util["statistic_p95_pct"])
    if avg < IDLE_CPU_AVG_MAX and p95 < IDLE_CPU_P95_MAX:
        return None  # idle: stopping beats scheduling
    ratio = util.get("business_hours_ratio")
    if ratio is None or ratio < SCHEDULE_BUSINESS_RATIO_MIN:
        return None  # no hourly-pattern evidence: not evaluated
    checks.append(_check("business_hours_ratio", True, ratio, f">= {SCHEDULE_BUSINESS_RATIO_MIN}"))
    off_fraction = (HOURS_PER_WEEK - SCHEDULED_HOURS_PER_WEEK) / HOURS_PER_WEEK
    savings = _money(r.hourly_cost * HOURS_PER_MONTH * off_fraction)
    return Finding(
        category="nonprod_scheduling",
        title=f"Run {r.name or r.resource_id} only during business hours",
        summary=f"{ratio:.0%} of CPU activity falls in weekday business hours, but the "
        "instance runs 24/7.",
        resource=r,
        eligibility=checks,
        evidence={
            "resource": _resource_evidence(r),
            "utilization": util,
            "hourly_price": str(r.hourly_cost),
        },
        assumptions=[
            f"Price: {PRICE_SOURCE}.",
            "Schedule: 12 hours x 5 weekdays = 60 of 168 weekly hours running.",
            "EBS storage keeps billing while stopped.",
        ],
        confidence="medium",
        risk="low",
        savings_formula="hourly_price x 730 h x (108 / 168)",
        estimated_monthly_savings=savings,
        utilization_window={
            "days": util["window_days"],
            "period_seconds": util.get("period_seconds"),
        },
    )


def evaluate_resource(r: ResourceInventory, now: datetime | None = None) -> list[Finding]:
    now = now or datetime.now(UTC)
    findings = [
        idle_nonprod_instance(r),
        underutilized_compute(r),
        unattached_volume(r, now),
        unused_elastic_ip(r),
        nonprod_scheduling(r),
    ]
    return [f for f in findings if f is not None]
