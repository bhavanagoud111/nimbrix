"""Run the detector across scopes, attach evidence, and persist anomalies with dedupe."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nimbrix_api.core.audit import record_audit
from nimbrix_api.models import Anomaly, CostLineItem
from nimbrix_api.modules.costs import service as costs
from nimbrix_api.modules.ml.anomaly import (
    DETECTOR_VERSION,
    THRESHOLDS,
    DetectedWindow,
    Sensitivity,
    detect,
)
from nimbrix_api.modules.ml.features import to_series

HISTORY_DAYS = 120
EVALUATE_LAST_DAYS = 21
MIN_SCOPE_SHARE = Decimal("0.01")


def _money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def scopes_for(
    session: Session, tenant_id: uuid.UUID, as_of: date, currency: str
) -> list[tuple[str, str]]:
    """Total plus every service/account/team with at least 1% of the last 30 days' spend."""
    window = costs.Window(as_of - timedelta(days=29), as_of)
    filters = costs.CostFilters(currency=currency)
    total = costs.sum_amount(session, costs.conditions(tenant_id, filters, window))
    scopes: list[tuple[str, str]] = [("total", "total")]
    if total <= 0:
        return scopes
    for dimension in ("service", "account", "team"):
        grouped = costs.grouped(session, costs.conditions(tenant_id, filters, window), dimension)
        for key, amount in sorted(grouped.items(), key=lambda kv: kv[1], reverse=True):
            if key != costs.UNALLOCATED and amount / total >= MIN_SCOPE_SHARE:
                scopes.append((dimension, key))
    return scopes


def _contributors(
    session: Session,
    tenant_id: uuid.UUID,
    scope_type: str,
    scope_value: str,
    window: DetectedWindow,
    currency: str,
) -> list[dict[str, Any]]:
    filters = costs.filters_for_scope(
        scope_type, None if scope_type == "total" else scope_value, currency
    )
    baseline = costs.Window(window.start - timedelta(days=28), window.start - timedelta(days=1))
    current = costs.Window(window.start, window.end)

    def per_resource(w: costs.Window) -> dict[str, Decimal]:
        rows = session.execute(
            select(CostLineItem.resource_id, func.sum(CostLineItem.amount))
            .where(*costs.conditions(tenant_id, filters, w), CostLineItem.resource_id.is_not(None))
            .group_by(CostLineItem.resource_id)
        ).all()
        return {rid: amount / w.days for rid, amount in rows if rid is not None}

    now_avg, before_avg = per_resource(current), per_resource(baseline)
    first_seen = dict(
        session.execute(
            select(CostLineItem.resource_id, func.min(CostLineItem.usage_date))
            .where(CostLineItem.tenant_id == tenant_id, CostLineItem.resource_id.in_(list(now_avg)))
            .group_by(CostLineItem.resource_id)
        ).all()
    )
    deltas = sorted(
        ((rid, now_avg[rid] - before_avg.get(rid, Decimal(0))) for rid in now_avg),
        key=lambda kv: kv[1],
        reverse=True,
    )
    return [
        {
            "evidence_id": f"resource:{rid}",
            "resource_id": rid,
            "avg_daily_in_window": str(now_avg[rid].quantize(Decimal("0.01"))),
            "avg_daily_baseline": str(before_avg.get(rid, Decimal(0)).quantize(Decimal("0.01"))),
            "delta_per_day": str(delta.quantize(Decimal("0.01"))),
            "first_seen": str(first_seen.get(rid)),
            "new_in_window": bool(first_seen.get(rid) and first_seen[rid] >= window.start),
        }
        for rid, delta in deltas[:8]
        if delta > 0
    ]


def detect_for_tenant(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    sensitivity: Sensitivity = "medium",
    currency: str = "USD",
) -> dict[str, int]:
    as_of = costs.as_of(session, tenant_id)
    counts = {"scopes": 0, "created": 0, "updated": 0}
    if as_of is None:
        return counts
    now = datetime.now(UTC)
    start = as_of - timedelta(days=HISTORY_DAYS - 1)
    for scope_type, scope_value in scopes_for(session, tenant_id, as_of, currency):
        counts["scopes"] += 1
        daily = costs.daily_totals(
            session,
            tenant_id,
            scope_type=scope_type,
            scope_value=None if scope_type == "total" else scope_value,
            currency=currency,
            start=start,
            end=as_of,
        )
        prepared = to_series(daily, as_of)
        if prepared.first_day is None or len(prepared.values) < 35:
            continue
        windows = detect(
            prepared.values.to_numpy(dtype=float),
            prepared.first_day,
            sensitivity=sensitivity,
            evaluate_last=EVALUATE_LAST_DAYS,
        )
        for window in windows:
            created = _persist(
                session, tenant_id, scope_type, scope_value, window, sensitivity, currency, now
            )
            counts["created" if created else "updated"] += 1
    session.flush()
    return counts


def _persist(
    session: Session,
    tenant_id: uuid.UUID,
    scope_type: str,
    scope_value: str,
    window: DetectedWindow,
    sensitivity: Sensitivity,
    currency: str,
    now: datetime,
) -> bool:
    existing = session.scalar(
        select(Anomaly)
        .where(
            Anomaly.tenant_id == tenant_id,
            Anomaly.detector_version == DETECTOR_VERSION,
            Anomaly.scope_type == scope_type,
            Anomaly.scope_value == scope_value,
            Anomaly.window_start <= window.end,
            Anomaly.window_end >= window.start - timedelta(days=1),
        )
        .order_by(Anomaly.window_start)
        .limit(1)
    )
    threshold = THRESHOLDS[sensitivity]
    evidence = {
        "method": DETECTOR_VERSION,
        "sensitivity": sensitivity,
        "threshold": threshold,
        "daily": [
            {
                "evidence_id": f"cost:{scope_type}:{scope_value}:{d.day}",
                "date": str(d.day),
                "observed": str(_money(d.observed)),
                "expected": str(_money(d.expected)),
                "score": round(d.score, 2),
            }
            for d in window.days
        ],
        "baseline": "median of the same weekday over the previous 4 weeks",
        "cost_query": {
            "scope_type": scope_type,
            "scope_value": scope_value,
            "start": str(window.start),
            "end": str(window.end),
            "currency": currency,
        },
        "contributors": _contributors(
            session, tenant_id, scope_type, scope_value, window, currency
        ),
    }
    values = {
        "window_start": min(window.start, existing.window_start) if existing else window.start,
        "window_end": max(window.end, existing.window_end) if existing else window.end,
        "expected": _money(window.expected),
        "observed": _money(window.observed),
        "score": round(window.max_score, 3),
        "severity": window.severity(threshold),
        "evidence": evidence,
        "last_detected_at": now,
        "currency": currency,
    }
    if existing is not None:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    anomaly = Anomaly(
        tenant_id=tenant_id,
        dedupe_key=f"{DETECTOR_VERSION}:{scope_type}:{scope_value}:{window.start}",
        detector_version=DETECTOR_VERSION,
        scope_type=scope_type,
        scope_value=scope_value,
        status="open",
        first_detected_at=now,
        **values,
    )
    session.add(anomaly)
    session.flush()
    record_audit(
        session,
        event_type="anomaly.detected",
        tenant_id=tenant_id,
        actor_type="system",
        target_type="anomaly",
        target_id=anomaly.id,
        payload={
            "scope": f"{scope_type}:{scope_value}",
            "window_start": str(window.start),
            "severity": anomaly.severity,
            "score": anomaly.score,
        },
    )
    return True
