"""Forecasting models: the seasonal-naive baseline and damped additive Holt-Winters.

Why Holt-Winters: cloud spend is dominated by a slowly moving level, weekly seasonality
(weekday/weekend workloads), and occasional trend; damped additive exponential smoothing
models exactly those components, is cheap to refit per scope, and is interpretable. It must
beat the seasonal-naive baseline in rolling-origin backtests to be selected.
"""

from __future__ import annotations

import warnings
from typing import Protocol

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from nimbrix_api.modules.ml.features import SEASON, winsorize_for_training


class Forecaster(Protocol):
    name: str
    version: str

    def fit(self, train: pd.Series) -> Forecaster: ...

    def predict(self, horizon: int) -> np.ndarray: ...


class SeasonalNaive:
    name = "seasonal_naive"
    version = "seasonal-naive-7d-v1"

    def __init__(self) -> None:
        self._last_season: np.ndarray | None = None

    def fit(self, train: pd.Series) -> SeasonalNaive:
        values = train.to_numpy(dtype=float)
        if len(values) < SEASON:
            raise ValueError("seasonal naive needs at least one full week")
        self._last_season = values[-SEASON:]
        return self

    def predict(self, horizon: int) -> np.ndarray:
        assert self._last_season is not None  # noqa: S101
        reps = int(np.ceil(horizon / SEASON))
        return np.tile(self._last_season, reps)[:horizon]


class HoltWinters:
    name = "holt_winters"
    version = "hw-damped-additive-7d-v1"

    def __init__(self) -> None:
        self._fitted: object | None = None
        self.convergence_warnings = 0
        self.clipped_outliers = 0

    def fit(self, train: pd.Series) -> HoltWinters:
        if len(train) < 3 * SEASON:
            raise ValueError("Holt-Winters needs at least three weeks")
        clean, self.clipped_outliers = winsorize_for_training(train)
        model = ExponentialSmoothing(
            clean.to_numpy(dtype=float),
            trend="add",
            damped_trend=True,
            seasonal="add",
            seasonal_periods=SEASON,
            initialization_method="estimated",
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._fitted = model.fit(optimized=True)
        self.convergence_warnings = sum(1 for w in caught if "converge" in str(w.message).lower())
        return self

    def predict(self, horizon: int) -> np.ndarray:
        assert self._fitted is not None  # noqa: S101
        return np.asarray(self._fitted.forecast(horizon), dtype=float)  # type: ignore[attr-defined]


MODEL_FACTORIES: dict[str, type[SeasonalNaive] | type[HoltWinters]] = {
    SeasonalNaive.name: SeasonalNaive,
    HoltWinters.name: HoltWinters,
}
