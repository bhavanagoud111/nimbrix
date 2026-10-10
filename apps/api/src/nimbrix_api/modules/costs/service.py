"""Deterministic cost analytics over normalized line items.

Conventions (documented in docs/analytics.md):

* All dates are UTC calendar days (``usage_date``). Months are calendar months
  ``[first day, first day of next month)``.
* The current UTC day is always partial (billing data lags), so default windows end at the
  latest **complete** day: ``min(latest usage_date, today_utc - 1)``.
* Amounts are exact decimals. Different currencies are never added together: every
  aggregate is grouped by currency, and series/breakdowns are computed for one currency.
* ``team``/``environment`` NULL means unallocated (no tag).
"""

from __future__ import annotations

import calendar
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from nimbrix_api.core.errors import ApiError
from nimbrix_api.models import BillingImport, CostLineItem

Dimension = Literal["account", "service", "region", "environment", "team", "resource"]
UNALLOCATED = "(unallocated)"
OTHER = "(other)"
ZERO = Decimal(0)

DIMENSION_COLUMNS: dict[str, Any] = {
    "account": CostLineItem.account_id,
    "service": CostLineItem.service,
    "region": CostLineItem.region,
    "environment": CostLineItem.environment,
    "team": CostLineItem.team,
    "resource": CostLineItem.resource_id,
}
MAX_WINDOW_DAYS = 400


@dataclass(frozen=True)
class CostFilters:
    start: date | None = None
    end: date | None = None
    account_id: str | None = None
    service: str | None = None
    region: str | None = None
    environment: str | None = None
    team: str | None = None
    tag_key: str | None = None
    tag_value: str | None = None
    currency: str = "USD"


@dataclass(frozen=True)
class Window:
    start: date
    end: date  # inclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Window:
        return Window(self.start - timedelta(days=self.days), self.start - timedelta(days=1))


def latest_usage_date(session: Session, tenant_id: uuid.UUID) -> date | None:
    return session.scalar(
        select(func.max(CostLineItem.usage_date)).where(CostLineItem.tenant_id == tenant_id)
    )


def as_of(session: Session, tenant_id: uuid.UUID, today: date | None = None) -> date | None:
    """Latest complete day with data (never today's partial UTC day)."""
    latest = latest_usage_date(session, tenant_id)
    if latest is None:
        return None
    yesterday = (today or datetime.now(UTC).date()) - timedelta(days=1)
    return min(latest, yesterday)


def resolve_window(
    session: Session, tenant_id: uuid.UUID, filters: CostFilters, default_days: int = 30
) -> Window | None:
    end = filters.end or as_of(session, tenant_id)
    if end is None:
        return None
    start = filters.start or end - timedelta(days=default_days - 1)
    if start > end:
        raise ApiError(422, "invalid_window", "start must be on or before end")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise ApiError(422, "window_too_large", f"windows are limited to {MAX_WINDOW_DAYS} days")
    return Window(start, end)


def conditions(
    tenant_id: uuid.UUID, filters: CostFilters, window: Window, *, currency: bool = True
) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [
        CostLineItem.tenant_id == tenant_id,
        CostLineItem.usage_date >= window.start,
        CostLineItem.usage_date <= window.end,
    ]
    if currency:
        conds.append(CostLineItem.currency == filters.currency)
    for column, value in (
        (CostLineItem.account_id, filters.account_id),
        (CostLineItem.service, filters.service),
        (CostLineItem.region, filters.region),
    ):
        if value:
            conds.append(column == value)
    for column, value in (
        (CostLineItem.environment, filters.environment),
        (CostLineItem.team, filters.team),
    ):
        if value == UNALLOCATED:
            conds.append(column.is_(None))
        elif value:
            conds.append(column == value)
    if filters.tag_key:
        if filters.tag_value is not None:
            conds.append(CostLineItem.tags[filters.tag_key].astext == filters.tag_value)
        else:
            conds.append(CostLineItem.tags.has_key(filters.tag_key))
    return conds


def sum_amount(session: Session, conds: list[ColumnElement[bool]]) -> Decimal:
    return (
        session.scalar(select(func.coalesce(func.sum(CostLineItem.amount), ZERO)).where(*conds))
        or ZERO
    )


def totals_by_currency(
    session: Session, tenant_id: uuid.UUID, filters: CostFilters, window: Window
) -> dict[str, Decimal]:
    rows = session.execute(
        select(CostLineItem.currency, func.sum(CostLineItem.amount))
        .where(*conditions(tenant_id, filters, window, currency=False))
        .group_by(CostLineItem.currency)
    ).all()
    return dict(rows)


def pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return ((current - previous) / previous * 100).quantize(Decimal("0.01"))


def summary(session: Session, tenant_id: uuid.UUID, filters: CostFilters) -> dict[str, Any]:
    latest = as_of(session, tenant_id)
    window = resolve_window(session, tenant_id, filters)
    if window is None or latest is None:
        return {"has_data": False, "currency": filters.currency}

    conds = conditions(tenant_id, filters, window)
    total = sum_amount(session, conds)
    previous_window = window.previous()
    previous_total = sum_amount(session, conditions(tenant_id, filters, previous_window))
    allocated = sum_amount(session, [*conds, CostLineItem.team.is_not(None)])

    # Month-to-date vs the same days of the previous month (both clipped to complete days).
    month_start = latest.replace(day=1)
    mtd = sum_amount(
        session,
        conditions(tenant_id, replace(filters, start=None, end=None), Window(month_start, latest)),
    )
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    comparable_end = min(prev_month_start + timedelta(days=latest.day - 1), prev_month_end)
    prev_mtd = sum_amount(
        session,
        conditions(
            tenant_id,
            replace(filters, start=None, end=None),
            Window(prev_month_start, comparable_end),
        ),
    )

    drivers = _top_drivers(session, tenant_id, filters, window, previous_window)
    other_currencies = {
        c: a
        for c, a in totals_by_currency(session, tenant_id, filters, window).items()
        if c != filters.currency
    }
    return {
        "has_data": True,
        "currency": filters.currency,
        "as_of": latest,
        "window": {"start": window.start, "end": window.end, "days": window.days},
        "total": total,
        "daily_average": (total / window.days).quantize(Decimal("0.000001")),
        "previous_window": {"start": previous_window.start, "end": previous_window.end},
        "previous_total": previous_total,
        "change_pct": pct_change(total, previous_total),
        "allocated": allocated,
        "unallocated": total - allocated,
        "allocation_pct": (allocated / total * 100).quantize(Decimal("0.01")) if total else None,
        "month_to_date": {
            "start": month_start,
            "end": latest,
            "amount": mtd,
            "previous_start": prev_month_start,
            "previous_end": comparable_end,
            "previous_amount": prev_mtd,
            "change_pct": pct_change(mtd, prev_mtd),
            "days_in_month": calendar.monthrange(latest.year, latest.month)[1],
        },
        "top_drivers": drivers,
        "other_currencies": [
            {"currency": c, "total": a} for c, a in sorted(other_currencies.items())
        ],
    }


def grouped(
    session: Session, conds: list[ColumnElement[bool]], dimension: str
) -> dict[str, Decimal]:
    column = DIMENSION_COLUMNS[dimension]
    rows = session.execute(
        select(column, func.sum(CostLineItem.amount)).where(*conds).group_by(column)
    ).all()
    return {(key if key is not None else UNALLOCATED): amount for key, amount in rows}


def _top_drivers(
    session: Session,
    tenant_id: uuid.UUID,
    filters: CostFilters,
    window: Window,
    previous: Window,
    limit: int = 5,
) -> list[dict[str, Any]]:
    current = grouped(session, conditions(tenant_id, filters, window), "service")
    before = grouped(session, conditions(tenant_id, filters, previous), "service")
    rows: list[dict[str, Any]] = [
        {
            "dimension": "service",
            "key": key,
            "amount": current.get(key, ZERO),
            "previous_amount": before.get(key, ZERO),
            "delta": current.get(key, ZERO) - before.get(key, ZERO),
        }
        for key in set(current) | set(before)
    ]
    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    return rows[:limit]


def breakdown(
    session: Session, tenant_id: uuid.UUID, filters: CostFilters, dimension: Dimension, limit: int
) -> dict[str, Any]:
    window = resolve_window(session, tenant_id, filters)
    if window is None:
        return {
            "has_data": False,
            "currency": filters.currency,
            "dimension": dimension,
            "items": [],
        }
    current = grouped(session, conditions(tenant_id, filters, window), dimension)
    previous = grouped(session, conditions(tenant_id, filters, window.previous()), dimension)
    total = sum(current.values(), ZERO)
    ordered = sorted(current.items(), key=lambda kv: kv[1], reverse=True)
    items = [
        {
            "key": key,
            "amount": amount,
            "share_pct": (amount / total * 100).quantize(Decimal("0.01")) if total else None,
            "previous_amount": previous.get(key, ZERO),
            "change_pct": pct_change(amount, previous.get(key, ZERO)),
        }
        for key, amount in ordered[:limit]
    ]
    rest = ordered[limit:]
    if rest:
        other = sum((a for _, a in rest), ZERO)
        items.append(
            {
                "key": OTHER,
                "amount": other,
                "share_pct": (other / total * 100).quantize(Decimal("0.01")) if total else None,
                "previous_amount": sum((previous.get(k, ZERO) for k, _ in rest), ZERO),
                "change_pct": None,
            }
        )
    return {
        "has_data": True,
        "currency": filters.currency,
        "dimension": dimension,
        "window": {"start": window.start, "end": window.end, "days": window.days},
        "total": total,
        "items": items,
    }


def _month_start(day: date) -> date:
    return day.replace(day=1)


def timeseries(  # noqa: PLR0912
    session: Session,
    tenant_id: uuid.UUID,
    filters: CostFilters,
    *,
    granularity: Literal["day", "month"],
    group_by: Dimension | None,
    top: int,
) -> dict[str, Any]:
    window = resolve_window(session, tenant_id, filters, default_days=90)
    if window is None:
        return {"has_data": False, "currency": filters.currency, "series": [], "buckets": []}
    if granularity == "month":
        window = Window(_month_start(window.start), window.end)
    conds = conditions(tenant_id, filters, window)
    bucket = (
        CostLineItem.usage_date
        if granularity == "day"
        else func.date_trunc("month", CostLineItem.usage_date).cast(CostLineItem.usage_date.type)
    )
    key_column = DIMENSION_COLUMNS[group_by] if group_by else None
    columns: list[Any] = [bucket.label("bucket"), func.sum(CostLineItem.amount).label("amount")]
    stmt: Select[Any]
    if key_column is not None:
        stmt = select(*columns, key_column.label("key")).where(*conds).group_by(bucket, key_column)
    else:
        stmt = select(*columns).where(*conds).group_by(bucket)
    rows = session.execute(stmt).all()

    buckets: list[date] = []
    cursor = window.start
    while cursor <= window.end:
        buckets.append(cursor)
        if granularity == "day":
            cursor += timedelta(days=1)
        else:
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    values: dict[str, dict[date, Decimal]] = defaultdict(dict)
    for row in rows:
        key = "total" if key_column is None else (row.key if row.key is not None else UNALLOCATED)
        values[key][row.bucket] = values[key].get(row.bucket, ZERO) + row.amount

    totals = {k: sum(v.values(), ZERO) for k, v in values.items()}
    keep = sorted(totals, key=lambda k: totals[k], reverse=True)[:top]
    series: list[dict[str, Any]] = []
    other: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for key, points in values.items():
        if key in keep:
            series.append(
                {
                    "key": key,
                    "total": totals[key],
                    "points": [{"date": b, "amount": points.get(b, ZERO)} for b in buckets],
                }
            )
        else:
            for b, amount in points.items():
                other[b] += amount
    series.sort(key=lambda s: s["total"], reverse=True)
    if other:
        series.append(
            {
                "key": OTHER,
                "total": sum(other.values(), ZERO),
                "points": [{"date": b, "amount": other.get(b, ZERO)} for b in buckets],
            }
        )
    return {
        "has_data": True,
        "currency": filters.currency,
        "granularity": granularity,
        "group_by": group_by,
        "window": {"start": window.start, "end": window.end, "days": window.days},
        "partial_last_bucket": granularity == "month"
        and window.end
        != (window.end.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1),
        "buckets": buckets,
        "series": series,
    }


def unit_costs(
    session: Session, tenant_id: uuid.UUID, filters: CostFilters, limit: int = 25
) -> dict[str, Any]:
    """Cost per usage unit, only where the data carries a quantity in one consistent unit."""
    window = resolve_window(session, tenant_id, filters)
    if window is None:
        return {"has_data": False, "currency": filters.currency, "items": []}
    conds = conditions(tenant_id, filters, window)
    rows = session.execute(
        select(
            CostLineItem.service,
            CostLineItem.usage_unit,
            func.sum(CostLineItem.amount),
            func.sum(CostLineItem.usage_quantity),
        )
        .where(
            *conds, CostLineItem.usage_quantity.is_not(None), CostLineItem.usage_unit.is_not(None)
        )
        .group_by(CostLineItem.service, CostLineItem.usage_unit)
        .order_by(func.sum(CostLineItem.amount).desc())
        .limit(limit)
    ).all()
    items = [
        {
            "service": service_name,
            "usage_unit": unit,
            "amount": amount,
            "usage_quantity": quantity,
            "unit_cost": (amount / quantity).quantize(Decimal("0.00000001")) if quantity else None,
        }
        for service_name, unit, amount, quantity in rows
    ]
    return {
        "has_data": True,
        "currency": filters.currency,
        "window": {"start": window.start, "end": window.end, "days": window.days},
        "items": items,
        "note": "Unit costs use provider usage quantities. Business metrics (cost per customer, "
        "per request) need an external metric source and are not computed.",
    }


def dimension_values(session: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("account", "service", "region", "environment", "team"):
        column = DIMENSION_COLUMNS[name]
        values = session.scalars(
            select(column).where(CostLineItem.tenant_id == tenant_id).distinct().order_by(column)
        ).all()
        result[name] = [v if v is not None else UNALLOCATED for v in values]
    result["currencies"] = session.scalars(
        select(CostLineItem.currency).where(CostLineItem.tenant_id == tenant_id).distinct()
    ).all()
    tag_keys = session.scalars(
        select(func.jsonb_object_keys(CostLineItem.tags))
        .where(CostLineItem.tenant_id == tenant_id)
        .distinct()
    ).all()
    result["tag_keys"] = sorted(tag_keys)
    latest = latest_usage_date(session, tenant_id)
    earliest = session.scalar(
        select(func.min(CostLineItem.usage_date)).where(CostLineItem.tenant_id == tenant_id)
    )
    result["data_window"] = {"start": earliest, "end": latest, "as_of": as_of(session, tenant_id)}
    return result


def daily_totals(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    scope_type: str,
    scope_value: str | None,
    currency: str,
    start: date,
    end: date,
) -> dict[date, Decimal]:
    """Daily totals for one scope; days without records are absent (callers decide on gaps)."""
    filters = filters_for_scope(scope_type, scope_value, currency)
    conds = conditions(tenant_id, filters, Window(start, end))
    rows = session.execute(
        select(CostLineItem.usage_date, func.sum(CostLineItem.amount))
        .where(*conds)
        .group_by(CostLineItem.usage_date)
    ).all()
    return dict(rows)


def data_mode(session: Session, tenant_id: uuid.UUID) -> str:
    """demo | live | mixed | none — from the sources of successfully imported data."""
    sources = set(
        session.scalars(
            select(BillingImport.source)
            .where(
                BillingImport.tenant_id == tenant_id,
                BillingImport.state.in_(("succeeded", "partially_failed")),
            )
            .distinct()
        )
    )
    if not sources:
        return "none"
    if sources == {"demo"}:
        return "demo"
    return "mixed" if "demo" in sources else "live"


def filters_for_scope(scope_type: str, scope_value: str | None, currency: str) -> CostFilters:
    """Map an analysis scope (total/account/service/team/environment/region) to filters."""
    if scope_type == "total" or scope_value is None:
        return CostFilters(currency=currency)
    mapping = {
        "account": "account_id",
        "service": "service",
        "team": "team",
        "environment": "environment",
        "region": "region",
    }
    field_name = mapping.get(scope_type)
    if field_name is None:
        raise ValueError(f"unsupported scope_type {scope_type!r}")
    return replace(CostFilters(currency=currency), **{field_name: scope_value})  # type: ignore[arg-type]
