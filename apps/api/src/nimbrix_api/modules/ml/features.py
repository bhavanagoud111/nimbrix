"""Versioned feature preparation for daily cost series.

``daily-cost-v1``:

* the series is a contiguous UTC daily index from the first day with data to ``end``;
* a day without records inside that range is spend 0 and is counted in ``missing_days``
  (billing exports omit zero-spend days; a large count is surfaced as a data-quality warning);
* for **training only**, values are winsorized against a robust weekly-seasonal baseline
  (same-weekday median of the previous four weeks ± 3.5 scaled MADs) so a one-off spike
  does not distort the fitted level. Evaluation and anomaly detection use raw values;
* everything takes only data up to the training cut-off: callers split before calling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

FEATURE_VERSION = "daily-cost-v1"
SEASON = 7
WINSOR_K = 3.5
MAD_SCALE = 1.4826


@dataclass(frozen=True)
class PreparedSeries:
    values: pd.Series  # float, DatetimeIndex (daily, contiguous)
    missing_days: int
    first_day: date | None
    last_day: date


def to_series(daily: dict[date, Decimal], end: date, start: date | None = None) -> PreparedSeries:
    if not daily:
        return PreparedSeries(pd.Series(dtype=float), 0, None, end)
    first = min(daily) if start is None else max(start, min(daily))
    index = pd.date_range(first, end, freq="D")
    values = pd.Series(
        [float(daily.get(ts.date(), Decimal(0))) for ts in index], index=index, dtype=float
    )
    missing = sum(1 for ts in index if ts.date() not in daily)
    return PreparedSeries(values, missing, first, end)


def seasonal_baseline(values: np.ndarray, t: int, weeks: int = 4) -> float | None:
    """Median of the same weekday over up to ``weeks`` previous weeks (needs at least 2)."""
    lags = [values[t - SEASON * k] for k in range(1, weeks + 1) if t - SEASON * k >= 0]
    if len(lags) < 2:
        return None
    return float(np.median(lags))


def winsorize_for_training(series: pd.Series) -> tuple[pd.Series, int]:
    """Clip extreme deviations from the seasonal baseline; returns (clipped, n_clipped)."""
    values = series.to_numpy(dtype=float).copy()
    baselines = np.array([seasonal_baseline(values, t) or np.nan for t in range(len(values))])
    residuals = values - baselines
    finite = residuals[np.isfinite(residuals)]
    if finite.size < SEASON:
        return series, 0
    mad = float(np.median(np.abs(finite - np.median(finite)))) * MAD_SCALE
    if mad <= 0:
        return series, 0
    upper = baselines + WINSOR_K * mad
    lower = baselines - WINSOR_K * mad
    mask = np.isfinite(baselines) & ((values > upper) | (values < lower))
    clipped = np.where(mask, np.clip(values, lower, upper), values)
    return pd.Series(clipped, index=series.index), int(mask.sum())


def future_dates(last_day: date, horizon: int) -> list[date]:
    return [last_day + timedelta(days=i) for i in range(1, horizon + 1)]
