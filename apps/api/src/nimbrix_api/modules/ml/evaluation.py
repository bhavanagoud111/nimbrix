"""Offline evaluation on synthetic, labeled series (no database).

``python -m nimbrix_api.modules.ml.evaluation`` prints JSON with:

* detector precision / recall at the event level, false positives per 1,000 series-days,
  and detection latency (days from incident start to the first flagged day);
* forecast backtests (MAE, WAPE, interval coverage) for seasonal naive vs Holt-Winters.

Series are generated deterministically from seeds, so numbers are reproducible.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from nimbrix_api.modules.ml import backtest
from nimbrix_api.modules.ml.anomaly import Sensitivity, detect
from nimbrix_api.modules.ml.models import HoltWinters, SeasonalNaive

START = date(2026, 1, 1)


@dataclass(frozen=True)
class LabeledEvent:
    start: int
    end: int  # inclusive index
    kind: str
    magnitude: float


@dataclass
class LabeledSeries:
    values: np.ndarray
    events: list[LabeledEvent]


def make_series(seed: int, days: int = 150, events: int = 2) -> LabeledSeries:
    """Weekly seasonality + trend + multiplicative noise + injected spikes / level shifts."""
    rng = random.Random(seed)  # noqa: S311 - reproducible synthetic data
    base = rng.uniform(80, 400)
    weekend = rng.uniform(0.65, 0.95)
    growth = rng.uniform(-0.0005, 0.002)
    noise = rng.uniform(0.02, 0.06)
    values = np.array(
        [
            base
            * (1 + growth) ** t
            * (weekend if (START + timedelta(days=t)).weekday() >= 5 else 1.0)
            * max(0.6, rng.gauss(1.0, noise))
            for t in range(days)
        ]
    )
    labeled: list[LabeledEvent] = []
    candidates = list(range(45, days - 8))
    rng.shuffle(candidates)
    for start in candidates:
        if len(labeled) >= events:
            break
        if any(abs(start - e.start) < 21 for e in labeled):
            continue
        kind = rng.choice(["spike", "level_shift"])
        magnitude = rng.uniform(0.25, 1.5)
        length = rng.randint(1, 3) if kind == "spike" else days - start
        end = min(days - 1, start + length - 1)
        values[start : end + 1] += base * magnitude
        # Label range: the spike itself, or the 4 weeks before a level shift becomes the new
        # seasonal baseline (flags inside that range are true positives, not false alarms).
        label_end = end if kind == "spike" else min(days - 1, start + 27)
        labeled.append(LabeledEvent(start=start, end=label_end, kind=kind, magnitude=magnitude))
    return LabeledSeries(values=values, events=sorted(labeled, key=lambda e: e.start))


def evaluate_detector(seeds: range, sensitivity: Sensitivity = "medium") -> dict[str, Any]:
    tp = fp = fn = 0
    latencies: list[int] = []
    missed_magnitudes: list[float] = []
    total_days = 0
    for seed in seeds:
        series = make_series(seed)
        total_days += len(series.values)
        windows = detect(series.values, START, sensitivity=sensitivity)
        flagged = [((w.start - START).days, (w.end - START).days) for w in windows]
        matched_windows: set[int] = set()
        for event in series.events:
            hits = [
                i for i, (ws, we) in enumerate(flagged) if ws <= event.end and we >= event.start
            ]
            matched_windows.update(hits)
            if not hits:
                fn += 1
                missed_magnitudes.append(event.magnitude)
            else:
                tp += 1
                latencies.append(max(0, flagged[hits[0]][0] - event.start))
        fp += len(flagged) - len(matched_windows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "sensitivity": sensitivity,
        "series": len(seeds),
        "labeled_events": tp + fn,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "false_positives_per_1000_days": round(fp / total_days * 1000, 3),
        "mean_detection_latency_days": round(float(np.mean(latencies)), 3) if latencies else None,
        "max_detection_latency_days": max(latencies) if latencies else None,
        "largest_missed_magnitude_x_base": round(max(missed_magnitudes), 3)
        if missed_magnitudes
        else None,
    }


def evaluate_forecasts(seeds: range, horizon: int, level: float = 0.8) -> dict[str, Any]:
    """Backtest both models on clean (event-free) series."""
    totals: dict[str, dict[str, list[float]]] = {
        SeasonalNaive.name: {"mae": [], "wape": [], "coverage": []},
        HoltWinters.name: {"mae": [], "wape": [], "coverage": []},
    }
    for seed in seeds:
        series = make_series(seed, days=180, events=0)
        values = pd.Series(
            series.values, index=pd.date_range(START, periods=len(series.values), freq="D")
        )
        cuts = backtest.origins(len(values), horizon, folds=6)
        for name in totals:
            result = backtest.evaluate(values, name, horizon, cuts, level)
            if result is None:
                continue
            totals[name]["mae"].append(result.mae)
            if result.wape is not None:
                totals[name]["wape"].append(result.wape)
            if result.coverage is not None:
                totals[name]["coverage"].append(result.coverage)
    return {
        "horizon_days": horizon,
        "interval_level": level,
        "series": len(seeds),
        "models": {
            name: {
                metric: round(float(np.mean(v)), 4) if v else None for metric, v in metrics.items()
            }
            for name, metrics in totals.items()
        },
    }


def main() -> None:
    report = {
        "detector": {s: evaluate_detector(range(200), s) for s in ("low", "medium", "high")},
        "forecast_7d": evaluate_forecasts(range(40), 7),
        "forecast_30d": evaluate_forecasts(range(40), 30),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
