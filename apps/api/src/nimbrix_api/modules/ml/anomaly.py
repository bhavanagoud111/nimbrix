"""Interpretable cost anomaly detection: seasonal baseline + robust residual threshold.

``seasonal-residual-v1``, per scope (total, service, account, team) and UTC day t:

* expected(t) = median of the same weekday in the previous 4 weeks (needs >= 2);
* scale(t) = 1.4826 x MAD of residuals over the previous 28 days, excluding days already
  flagged (so an ongoing incident does not hide itself), floored at max(5% of expected, $1);
* score(t) = (observed - expected) / scale;
* flagged when score >= threshold(sensitivity) **and** observed - expected >=
  max(min_delta_abs, min_delta_pct x expected).

Consecutive flagged days for a scope form one anomaly window. Only increases are detected
(spend drops are a data-quality concern handled elsewhere).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

import numpy as np

from nimbrix_api.modules.ml.features import MAD_SCALE, seasonal_baseline

DETECTOR_VERSION = "seasonal-residual-v1"
Sensitivity = Literal["low", "medium", "high"]
THRESHOLDS: dict[str, float] = {"low": 5.0, "medium": 3.5, "high": 2.5}
MIN_DELTA_ABS = 5.0
MIN_DELTA_PCT = 0.2
HISTORY_DAYS = 28


@dataclass
class DayFlag:
    day: date
    observed: float
    expected: float
    score: float


@dataclass
class DetectedWindow:
    start: date
    end: date
    days: list[DayFlag] = field(default_factory=list)

    @property
    def observed(self) -> float:
        return sum(d.observed for d in self.days)

    @property
    def expected(self) -> float:
        return sum(d.expected for d in self.days)

    @property
    def max_score(self) -> float:
        return max(d.score for d in self.days)

    def severity(self, threshold: float) -> str:
        excess = (self.observed - self.expected) / self.expected if self.expected > 0 else 1.0
        if self.max_score >= 2 * threshold or excess >= 0.5:
            return "high"
        if self.max_score >= 1.4 * threshold or excess >= 0.25:
            return "medium"
        return "low"


def detect(
    values: np.ndarray,
    first_day: date,
    *,
    sensitivity: Sensitivity = "medium",
    evaluate_last: int | None = None,
    min_delta_abs: float = MIN_DELTA_ABS,
) -> list[DetectedWindow]:
    threshold = THRESHOLDS[sensitivity]
    n = len(values)
    start_t = HISTORY_DAYS if evaluate_last is None else max(HISTORY_DAYS, n - evaluate_last)
    flagged = np.zeros(n, dtype=bool)
    flags: list[DayFlag | None] = [None] * n
    # Flag pass over history first so the scale excludes earlier incidents.
    for t in range(HISTORY_DAYS, n):
        expected = seasonal_baseline(values, t)
        if expected is None:
            continue
        residuals = []
        for j in range(t - HISTORY_DAYS, t):
            if flagged[j]:
                continue
            base_j = seasonal_baseline(values, j)
            if base_j is not None:
                residuals.append(values[j] - base_j)
        if len(residuals) < 7:
            continue
        r = np.asarray(residuals)
        scale = max(float(np.median(np.abs(r - np.median(r)))) * MAD_SCALE, 0.05 * expected, 1.0)
        delta = float(values[t]) - expected
        score = delta / scale
        if score >= threshold and delta >= max(min_delta_abs, MIN_DELTA_PCT * expected):
            flagged[t] = True
            flags[t] = DayFlag(first_day + timedelta(days=t), float(values[t]), expected, score)

    windows: list[DetectedWindow] = []
    current: DetectedWindow | None = None
    for t in range(n):
        flag = flags[t]
        if flag is None:
            current = None
            continue
        if current is None:
            current = DetectedWindow(start=flag.day, end=flag.day)
            windows.append(current)
        current.end = flag.day
        current.days.append(flag)
    return [w for w in windows if w.end >= first_day + timedelta(days=start_t)]
