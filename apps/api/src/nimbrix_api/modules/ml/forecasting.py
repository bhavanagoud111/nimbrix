"""Forecast runs: prepare features, backtest candidates, select, predict with intervals, persist."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from nimbrix_api.models import Forecast
from nimbrix_api.modules.costs import service as costs
from nimbrix_api.modules.ml import backtest
from nimbrix_api.modules.ml.features import FEATURE_VERSION, future_dates, to_series
from nimbrix_api.modules.ml.models import HoltWinters, SeasonalNaive

MIN_HISTORY_DAYS = 21
HISTORY_WINDOW_DAYS = 180
BACKTEST_FOLDS = 6
SUPPORTED_HORIZONS = (7, 30)


class InsufficientDataError(Exception):
    pass


def _q(value: float) -> str:
    return str(Decimal(str(max(value, 0.0))).quantize(Decimal("0.01")))


def run_forecast(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    scope_type: str,
    scope_value: str,
    horizon: int,
    currency: str = "USD",
    level: float = 0.8,
) -> Forecast:
    """Compute and persist one forecast. Insufficient history is persisted as such, not faked."""
    end = costs.as_of(session, tenant_id)
    base = Forecast(
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_value=scope_value,
        feature_version=FEATURE_VERSION,
        horizon_days=horizon,
        interval_level=level,
        currency=currency,
        points=[],
        metrics={},
    )
    if end is None:
        return _insufficient(session, base, "no cost data for this tenant")
    daily = costs.daily_totals(
        session,
        tenant_id,
        scope_type=scope_type,
        scope_value=None if scope_type == "total" else scope_value,
        currency=currency,
        start=end - timedelta(days=HISTORY_WINDOW_DAYS - 1),
        end=end,
    )
    prepared = to_series(daily, end)
    n = len(prepared.values)
    base.training_start = prepared.first_day
    base.training_end = end
    if n < MIN_HISTORY_DAYS:
        return _insufficient(
            session, base, f"{n} days of history; at least {MIN_HISTORY_DAYS} are required"
        )

    cuts = backtest.origins(n, horizon, BACKTEST_FOLDS)
    evaluations = {}
    if len(cuts) >= 3:
        for name in (SeasonalNaive.name, HoltWinters.name):
            result = backtest.evaluate(prepared.values, name, horizon, cuts, level)
            if result is not None:
                evaluations[name] = result

    baseline = evaluations.get(SeasonalNaive.name)
    candidate = evaluations.get(HoltWinters.name)
    selected = SeasonalNaive.name
    if (
        baseline
        and candidate
        and candidate.wape is not None
        and baseline.wape is not None
        and candidate.wape < baseline.wape
    ):
        selected = HoltWinters.name

    model = SeasonalNaive() if selected == SeasonalNaive.name else HoltWinters()
    model.fit(prepared.values)
    point = np.maximum(model.predict(horizon), 0.0)
    chosen_eval = evaluations.get(selected)
    if chosen_eval is not None:
        half = backtest.half_widths(chosen_eval, level)
        interval_method = "cross-conformal (rolling-origin backtest residuals)"
    else:
        # Limited history: week-over-week naive residuals; coverage is unverified.
        values = prepared.values.to_numpy(dtype=float)
        diffs = np.abs(values[7:] - values[:-7]) if n > 7 else np.array([0.0])
        half = np.full(horizon, float(np.quantile(diffs, level)))
        interval_method = "week-over-week residual quantile (limited history, coverage unverified)"

    days = future_dates(end, horizon)
    base.points = [
        {"date": str(d), "yhat": _q(p), "lower": _q(p - h), "upper": _q(p + h)}
        for d, p, h in zip(days, point, half, strict=True)
    ]
    base.model_name = selected
    base.model_version = model.version
    base.status = "succeeded"
    metrics: dict[str, Any] = {
        "selected_model": selected,
        "selection_rule": "lowest backtest WAPE; seasonal naive unless the alternative beats it",
        "models": {name: e.as_dict() for name, e in evaluations.items()},
        "history_days": n,
        "missing_days": prepared.missing_days,
        "interval_method": interval_method,
        "backtest": {"folds": len(cuts), "step_days": 7, "horizon_days": horizon},
    }
    if baseline and candidate and baseline.wape and candidate.wape is not None:
        metrics["improvement_vs_baseline_pct"] = round(
            (baseline.wape - candidate.wape) / baseline.wape * 100, 2
        )
    if isinstance(model, HoltWinters):
        metrics["outliers_clipped"] = model.clipped_outliers
        metrics["convergence_warnings"] = model.convergence_warnings
    if not evaluations:
        base.message = "Limited history: baseline model only; no backtest metrics are available."
    base.metrics = metrics
    session.add(base)
    session.flush()
    return base


def _insufficient(session: Session, base: Forecast, message: str) -> Forecast:
    base.model_name = "none"
    base.model_version = "none"
    base.status = "insufficient_data"
    base.message = message
    session.add(base)
    session.flush()
    return base


def latest_forecast(
    session: Session, tenant_id: uuid.UUID, scope_type: str, scope_value: str, horizon: int
) -> Forecast | None:
    return session.scalar(
        select(Forecast)
        .where(
            Forecast.tenant_id == tenant_id,
            Forecast.scope_type == scope_type,
            Forecast.scope_value == scope_value,
            Forecast.horizon_days == horizon,
        )
        .order_by(Forecast.created_at.desc())
        .limit(1)
    )


def ensure_forecast(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    scope_type: str,
    scope_value: str,
    horizon: int,
    currency: str = "USD",
) -> tuple[Forecast, bool]:
    """Reuse a forecast already trained through the current as_of date (no duplicate work)."""
    end = costs.as_of(session, tenant_id)
    existing = latest_forecast(session, tenant_id, scope_type, scope_value, horizon)
    if (
        existing is not None
        and end is not None
        and existing.training_end == end
        and existing.currency == currency
    ):
        return existing, False
    return run_forecast(
        session,
        tenant_id,
        scope_type=scope_type,
        scope_value=scope_value,
        horizon=horizon,
        currency=currency,
    ), True


def remaining_days_in_month(as_of: date) -> int:
    next_month = (as_of.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (next_month - as_of).days - 1
