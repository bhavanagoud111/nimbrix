"""Rolling-origin backtesting with cross-conformal prediction intervals.

For fold i the model is trained on data up to origin_i only (no leakage) and evaluated on the
next ``horizon`` days. Interval half-widths for horizon step k are the ``level`` quantile of
absolute step-k errors from the *other* folds, which yields an honest coverage estimate for
the held-out fold. The final forecast uses quantiles from all folds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from nimbrix_api.modules.ml.models import MODEL_FACTORIES


@dataclass
class ModelEvaluation:
    name: str
    version: str
    folds: int
    mae: float
    wape: float | None
    coverage: float | None
    abs_errors: np.ndarray = field(repr=False)  # shape (folds, horizon)
    failed_folds: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.version,
            "folds": self.folds,
            "mae": round(self.mae, 4),
            "wape": round(self.wape, 4) if self.wape is not None else None,
            "interval_coverage": round(self.coverage, 4) if self.coverage is not None else None,
            "failed_folds": self.failed_folds,
        }


def conformal_quantile(abs_errors: np.ndarray, level: float) -> np.ndarray:
    """Per-step split-conformal quantile with the finite-sample correction ceil((n+1)L)/n."""
    n = abs_errors.shape[0]
    q = min(1.0, float(np.ceil((n + 1) * level)) / n)
    return np.asarray(np.quantile(abs_errors, q, axis=0, method="higher"), dtype=float)


def origins(n: int, horizon: int, folds: int, step: int = 7, min_train: int = 28) -> list[int]:
    """Training cut-offs (exclusive indexes), latest last, each leaving a full horizon."""
    last = n - horizon
    cuts = [last - step * i for i in range(folds)]
    return sorted(c for c in cuts if c >= min_train)


def evaluate(
    series: pd.Series, model_name: str, horizon: int, cuts: list[int], level: float
) -> ModelEvaluation | None:
    values = series.to_numpy(dtype=float)
    errors: list[np.ndarray] = []
    actual_sums: list[float] = []
    failed = 0
    version = ""
    for cut in cuts:
        model = MODEL_FACTORIES[model_name]()
        version = model.version
        try:
            prediction = np.maximum(model.fit(series.iloc[:cut]).predict(horizon), 0.0)
        except (ValueError, np.linalg.LinAlgError):
            failed += 1
            continue
        actual = values[cut : cut + horizon]
        errors.append(actual - prediction)
        actual_sums.append(float(np.abs(actual).sum()))
    if not errors:
        return None
    err = np.vstack(errors)
    abs_err = np.abs(err)
    total_actual = sum(actual_sums)
    coverage = None
    if len(errors) >= 3:
        hits, count = 0, 0
        for i in range(len(errors)):
            others = np.delete(abs_err, i, axis=0)
            half_width = conformal_quantile(others, level)
            hits += int((abs_err[i] <= half_width).sum())
            count += horizon
        coverage = hits / count
    return ModelEvaluation(
        name=model_name,
        version=version,
        folds=len(errors),
        mae=float(abs_err.mean()),
        wape=float(abs_err.sum() / total_actual) if total_actual > 0 else None,
        coverage=coverage,
        abs_errors=abs_err,
        failed_folds=failed,
    )


def half_widths(evaluation: ModelEvaluation, level: float) -> np.ndarray:
    return conformal_quantile(evaluation.abs_errors, level)
