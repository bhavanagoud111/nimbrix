"""Budget utilization (actual and projected) and threshold alerts for the current month.

"Current month" is the calendar month of the latest complete data day (``as_of``), so a
demo dataset ending yesterday evaluates the month it is in. The end-of-month projection
uses the persisted ML forecast for the scope when one exists (method ``forecast``);
otherwise a deterministic run rate (``run_rate``: MTD / elapsed days x days in month).
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nimbrix_api.core.audit import record_audit
from nimbrix_api.models import Budget, BudgetAlert, Forecast
from nimbrix_api.modules.costs import service as costs

RATIO = Decimal("0.0001")


@dataclass(frozen=True)
class BudgetStatus:
    budget: Budget
    period_start: date
    period_end: date
    as_of: date | None
    actual: Decimal
    projected: Decimal | None
    projection_method: str | None
    utilization: Decimal
    projected_utilization: Decimal | None
    thresholds_crossed: list[Decimal]
    projected_thresholds_crossed: list[Decimal]

    def as_dict(self) -> dict[str, Any]:
        b = self.budget
        return {
            "id": b.id,
            "name": b.name,
            "scope_type": b.scope_type,
            "scope_value": b.scope_value,
            "period": b.period,
            "amount": b.amount,
            "currency": b.currency,
            "alert_thresholds": [Decimal(str(t)) for t in b.alert_thresholds],
            "owner": b.owner,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "as_of": self.as_of,
            "actual": self.actual,
            "projected": self.projected,
            "projection_method": self.projection_method,
            "utilization": self.utilization,
            "projected_utilization": self.projected_utilization,
            "status": self.status,
        }

    @property
    def status(self) -> str:
        if self.utilization >= 1:
            return "over_budget"
        if self.projected_utilization is not None and self.projected_utilization >= 1:
            return "at_risk"
        if self.thresholds_crossed:
            return "warning"
        return "on_track"


def _scope_filters(budget: Budget) -> costs.CostFilters:
    return costs.filters_for_scope(budget.scope_type, budget.scope_value, budget.currency)


def _forecast_projection(
    session: Session, budget: Budget, as_of: date, period_end: date
) -> Decimal | None:
    scope_value = budget.scope_value if budget.scope_type != "total" else "total"
    forecast = session.scalar(
        select(Forecast)
        .where(
            Forecast.tenant_id == budget.tenant_id,
            Forecast.scope_type == budget.scope_type,
            Forecast.scope_value == scope_value,
            Forecast.status == "succeeded",
            Forecast.currency == budget.currency,
            Forecast.training_end == as_of,
        )
        .order_by(Forecast.created_at.desc())
        .limit(1)
    )
    if forecast is None:
        return None
    remaining = [
        Decimal(str(p["yhat"]))
        for p in forecast.points
        if as_of < date.fromisoformat(p["date"]) <= period_end
    ]
    if len(remaining) < (period_end - as_of).days:
        return None  # horizon too short to cover the rest of the month
    return sum(remaining, Decimal(0))


def evaluate(session: Session, budget: Budget) -> BudgetStatus:
    as_of = costs.as_of(session, budget.tenant_id)
    reference = as_of or datetime.now(UTC).date()
    period_start = reference.replace(day=1)
    days_in_month = calendar.monthrange(reference.year, reference.month)[1]
    period_end = period_start + timedelta(days=days_in_month - 1)
    actual = Decimal(0)
    projected: Decimal | None = None
    method: str | None = None
    if as_of is not None:
        filters = _scope_filters(budget)
        conds = costs.conditions(budget.tenant_id, filters, costs.Window(period_start, as_of))
        actual = costs.sum_amount(session, conds)
        remaining = _forecast_projection(session, budget, as_of, period_end)
        if remaining is not None:
            projected, method = actual + remaining, "forecast"
        else:
            elapsed = (as_of - period_start).days + 1
            projected = (actual / elapsed * days_in_month).quantize(Decimal("0.000001"))
            method = "run_rate"
    utilization = (actual / budget.amount).quantize(RATIO, rounding=ROUND_HALF_UP)
    projected_utilization = (
        (projected / budget.amount).quantize(RATIO, rounding=ROUND_HALF_UP)
        if projected is not None
        else None
    )
    thresholds = sorted(Decimal(str(t)) for t in budget.alert_thresholds)
    return BudgetStatus(
        budget=budget,
        period_start=period_start,
        period_end=period_end,
        as_of=as_of,
        actual=actual,
        projected=projected,
        projection_method=method,
        utilization=utilization,
        projected_utilization=projected_utilization,
        thresholds_crossed=[t for t in thresholds if utilization >= t],
        projected_thresholds_crossed=[
            t
            for t in thresholds
            if projected_utilization is not None and projected_utilization >= t
        ],
    )


def evaluate_alerts(session: Session, tenant_id: uuid.UUID) -> int:
    """Create one alert per (budget, month, threshold, kind) the first time it is crossed."""
    created = 0
    for budget in session.scalars(select(Budget).where(Budget.tenant_id == tenant_id)):
        status = evaluate(session, budget)
        crossed = [
            ("actual", t, status.utilization, status.actual) for t in status.thresholds_crossed
        ]
        if status.projected is not None and status.projected_utilization is not None:
            crossed += [
                ("forecast", t, status.projected_utilization, status.projected)
                for t in status.projected_thresholds_crossed
            ]
        for kind, threshold, utilization, amount in crossed:
            result = session.execute(
                insert(BudgetAlert)
                .values(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    budget_id=budget.id,
                    period_start=status.period_start,
                    threshold=threshold,
                    kind=kind,
                    utilization=utilization,
                    amount=amount,
                    currency=budget.currency,
                    status="open",
                    details={
                        "projection_method": status.projection_method,
                        "as_of": str(status.as_of),
                    },
                )
                .on_conflict_do_nothing(
                    index_elements=["budget_id", "period_start", "threshold", "kind"]
                )
                .returning(BudgetAlert.id)
            ).scalar_one_or_none()
            if result is not None:
                created += 1
                record_audit(
                    session,
                    event_type="budget.alert_raised",
                    tenant_id=tenant_id,
                    actor_type="system",
                    target_type="budget",
                    target_id=budget.id,
                    payload={
                        "kind": kind,
                        "threshold": str(threshold),
                        "utilization": str(utilization),
                    },
                )
    session.flush()
    return created
