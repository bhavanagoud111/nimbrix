"""Persisting rule findings and simulating proposed changes (deterministic, no LLM)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nimbrix_api.models import CostLineItem, Recommendation, ResourceInventory
from nimbrix_api.modules.pricing import HOURS_PER_MONTH
from nimbrix_api.modules.recommendations.rules import RULE_VERSION, Finding, evaluate_resource

CENTS = Decimal("0.01")
# Statuses a refresh may overwrite; user decisions (dismissed, approval_requested, actioned) stick.
_REFRESHABLE = ("open", "insufficient_evidence", "superseded")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def refresh(session: Session, tenant_id: uuid.UUID, now: datetime | None = None) -> dict[str, int]:
    """Re-evaluate every rule over the tenant's inventory and upsert recommendations."""
    now = now or datetime.now(UTC)
    resources = session.scalars(
        select(ResourceInventory).where(ResourceInventory.tenant_id == tenant_id)
    ).all()
    findings: dict[str, Finding] = {}
    for resource in resources:
        for finding in evaluate_resource(resource, now):
            findings[finding.dedupe_key] = finding

    existing = {
        r.dedupe_key: r
        for r in session.scalars(
            select(Recommendation).where(Recommendation.tenant_id == tenant_id)
        )
    }
    counts = {"created": 0, "updated": 0, "superseded": 0}
    for key, f in findings.items():
        rec = existing.get(key)
        values: dict[str, Any] = {
            "category": f.category,
            "rule_version": RULE_VERSION,
            "title": f.title[:300],
            "summary": f.summary[:2000],
            "resource_id": f.resource.resource_id,
            "account_id": f.resource.account_id,
            "region": f.resource.region,
            "affected_resources": [f.resource.resource_id, *f.extra_resources],
            "evidence": f.evidence,
            "eligibility": f.eligibility,
            "assumptions": f.assumptions,
            "savings_formula": f.savings_formula,
            "estimated_monthly_savings": f.estimated_monthly_savings,
            "currency": f.resource.currency,
            "confidence": f.confidence,
            "risk": f.risk,
            "owner": f.resource.team or "unassigned",
            "utilization_window": f.utilization_window,
            "proposed_action": f.proposed_action,
            "last_evaluated_at": now,
        }
        status = "insufficient_evidence" if f.insufficient else "open"
        if rec is None:
            session.add(
                Recommendation(tenant_id=tenant_id, dedupe_key=key, status=status, **values)
            )
            counts["created"] += 1
        else:
            for field_name, value in values.items():
                setattr(rec, field_name, value)
            if rec.status in _REFRESHABLE:
                rec.status = status
            counts["updated"] += 1
    for key, rec in existing.items():
        if key not in findings and rec.status in ("open", "insufficient_evidence"):
            rec.status = "superseded"
            rec.last_evaluated_at = now
            counts["superseded"] += 1
    session.flush()
    return counts


def _observed_monthly(
    session: Session, tenant_id: uuid.UUID, resource_id: str, currency: str
) -> tuple[Decimal | None, int]:
    """Billed run rate from the last 7 days of line items for this resource (if any)."""
    latest = session.scalar(
        select(func.max(CostLineItem.usage_date)).where(
            CostLineItem.tenant_id == tenant_id, CostLineItem.resource_id == resource_id
        )
    )
    if latest is None:
        return None, 0
    rows = session.execute(
        select(CostLineItem.usage_date, func.sum(CostLineItem.amount))
        .where(
            CostLineItem.tenant_id == tenant_id,
            CostLineItem.resource_id == resource_id,
            CostLineItem.currency == currency,
            CostLineItem.usage_date > latest - timedelta(days=7),
        )
        .group_by(CostLineItem.usage_date)
    ).all()
    if not rows:
        return None, 0
    daily = sum((amount for _, amount in rows), Decimal(0)) / len(rows)
    return _money(daily * Decimal(365) / Decimal(12)), len(rows)


def _attached_storage(
    session: Session, tenant_id: uuid.UUID, instance_id: str
) -> list[dict[str, Any]]:
    volumes = session.scalars(
        select(ResourceInventory).where(
            ResourceInventory.tenant_id == tenant_id,
            ResourceInventory.resource_type == "ebs_volume",
            ResourceInventory.attributes["attached_instance_id"].astext == instance_id,
        )
    ).all()
    return [
        {
            "item": f"EBS volume {v.resource_id}",
            "monthly_amount": str(_money(v.monthly_cost)) if v.monthly_cost is not None else None,
            "reason": "storage keeps billing while the instance is stopped",
        }
        for v in volumes
    ]


def simulate(session: Session, rec: Recommendation, now: datetime | None = None) -> dict[str, Any]:
    """Estimate the monthly cost delta of applying a recommendation. Never executes anything."""
    now = now or datetime.now(UTC)
    if rec.estimated_monthly_savings is None or rec.status == "insufficient_evidence":
        return {
            "recommendation_id": str(rec.id),
            "mode": "simulation",
            "realized": False,
            "status": "insufficient_evidence",
            "message": "The recommendation lacks the evidence needed to estimate a cost change.",
            "simulated_at": now.isoformat(),
        }
    resource = session.scalar(
        select(ResourceInventory).where(
            ResourceInventory.tenant_id == rec.tenant_id,
            ResourceInventory.resource_id == rec.resource_id,
        )
    )
    currency = rec.currency
    list_monthly: Decimal | None = None
    if resource is not None and resource.hourly_cost is not None:
        list_monthly = _money(resource.hourly_cost * HOURS_PER_MONTH)
    elif resource is not None and resource.monthly_cost is not None:
        list_monthly = _money(resource.monthly_cost)
    observed_monthly, observed_days = _observed_monthly(
        session, rec.tenant_id, rec.resource_id or "", currency
    )
    current = list_monthly if list_monthly is not None else observed_monthly
    savings = rec.estimated_monthly_savings
    if current is None:
        current = savings
    projected = _money(max(current - savings, Decimal(0)))

    # Uncertainty: span the list-price estimate and the billed run rate when they differ,
    # widened by confidence. Both are evidence; neither is presented as exact.
    ratio = (observed_monthly / list_monthly) if observed_monthly and list_monthly else Decimal(1)
    widen = {"high": Decimal("0.05"), "medium": Decimal("0.15"), "low": Decimal("0.30")}.get(
        rec.confidence, Decimal("0.30")
    )
    candidates = [savings, _money(savings * ratio)]
    low = _money(min(candidates) * (1 - widen))
    high = _money(max(candidates) * (1 + widen))

    excluded: list[dict[str, Any]] = []
    if (
        rec.category in ("idle_nonprod_instance", "nonprod_scheduling", "underutilized_compute")
        and rec.resource_id
    ):
        excluded += _attached_storage(session, rec.tenant_id, rec.resource_id)
        excluded.append(
            {
                "item": "Data transfer and snapshots",
                "monthly_amount": None,
                "reason": "not attributable per instance in this data",
            }
        )
    if rec.category == "unattached_volume":
        excluded.append(
            {
                "item": "Snapshot storage (if a snapshot is kept)",
                "monthly_amount": None,
                "reason": "depends on changed blocks",
            }
        )

    return {
        "recommendation_id": str(rec.id),
        "mode": "simulation",
        "realized": False,
        "status": "estimated",
        "currency": currency,
        "current_monthly_cost": str(current),
        "projected_monthly_cost": str(projected),
        "estimated_monthly_savings": str(savings),
        "savings_range": {"low": str(low), "high": str(high)},
        "basis": {
            "list_price_monthly": str(list_monthly) if list_monthly is not None else None,
            "observed_billed_monthly_run_rate": str(observed_monthly)
            if observed_monthly is not None
            else None,
            "observed_days": observed_days,
            "savings_formula": rec.savings_formula,
        },
        "excluded_costs": excluded,
        "assumptions": rec.assumptions,
        "confidence": rec.confidence,
        "simulated_at": now.isoformat(),
    }
