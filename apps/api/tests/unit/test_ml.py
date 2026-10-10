"""Phase 4 offline tests: models, leakage-safe backtests, intervals, detector quality."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from nimbrix_api.modules.ml import backtest, evaluation
from nimbrix_api.modules.ml.anomaly import detect
from nimbrix_api.modules.ml.features import to_series, winsorize_for_training
from nimbrix_api.modules.ml.models import HoltWinters, SeasonalNaive

START = date(2026, 1, 1)


def _weekly(days: int, *, trend: float = 0.0, level: float = 100.0) -> pd.Series:
    pattern = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 0.7, 0.7])
    values = [level * (1 + trend * t) * pattern[t % 7] for t in range(days)]
    return pd.Series(values, index=pd.date_range(START, periods=days, freq="D"))


def test_seasonal_naive_repeats_last_week() -> None:
    series = _weekly(28)
    np.testing.assert_allclose(
        SeasonalNaive().fit(series).predict(10), list(series.iloc[-7:]) + list(series.iloc[-7:-4])
    )


def test_holt_winters_tracks_trend_better_than_naive() -> None:
    series = _weekly(120, trend=0.01)
    actual = _weekly(134, trend=0.01).iloc[-14:].to_numpy()
    naive_err = np.abs(SeasonalNaive().fit(series).predict(14) - actual).mean()
    hw_err = np.abs(HoltWinters().fit(series).predict(14) - actual).mean()
    assert hw_err < naive_err


def test_models_reject_too_little_history() -> None:
    with pytest.raises(ValueError, match="week"):
        SeasonalNaive().fit(_weekly(5))
    with pytest.raises(ValueError, match="three weeks"):
        HoltWinters().fit(_weekly(14))


def test_backtest_never_trains_on_test_data(monkeypatch: pytest.MonkeyPatch) -> None:
    series = _weekly(100)
    seen: list[int] = []
    original_fit = SeasonalNaive.fit

    def spy(self: SeasonalNaive, train: pd.Series) -> SeasonalNaive:
        seen.append(len(train))
        return original_fit(self, train)

    monkeypatch.setattr(SeasonalNaive, "fit", spy)
    cuts = backtest.origins(len(series), 7, folds=6)
    assert cuts == [58, 65, 72, 79, 86, 93]
    result = backtest.evaluate(series, "seasonal_naive", 7, cuts, 0.8)
    assert result is not None
    assert seen == cuts  # each fold trained strictly before its evaluation window
    assert result.mae == pytest.approx(0.0)  # perfectly seasonal series


def test_conformal_quantile_applies_finite_sample_correction() -> None:
    errors = np.arange(1, 6, dtype=float).reshape(5, 1)  # five folds
    assert backtest.conformal_quantile(errors, 0.8)[0] == 5.0  # ceil(6*0.8)/5 = 0.96 -> max
    many = np.arange(1, 101, dtype=float).reshape(100, 1)
    assert backtest.conformal_quantile(many, 0.8)[0] == 82.0  # q = ceil(101 x 0.8)/100 = 0.81


def test_winsorization_clips_isolated_spike_for_training_only() -> None:
    rng = np.random.default_rng(1)
    series = _weekly(60) * rng.normal(1.0, 0.03, 60)
    spiked = series.copy()
    spiked.iloc[45] *= 6
    clipped, n = winsorize_for_training(spiked)
    assert n >= 1
    assert clipped.iloc[45] < spiked.iloc[45]
    assert spiked.iloc[45] == series.iloc[45] * 6  # input untouched


def test_missing_days_are_zero_and_counted() -> None:
    daily = {START: Decimal(5), START + timedelta(days=3): Decimal(7)}
    prepared = to_series(daily, START + timedelta(days=4))
    assert list(prepared.values) == [5.0, 0.0, 0.0, 7.0, 0.0]
    assert prepared.missing_days == 3


def test_detector_flags_known_spike_on_day_one() -> None:
    values = np.array(_weekly(70).to_numpy(), dtype=float)
    values[60:] += 80  # level shift of +80 (~80%)
    windows = detect(values, START)
    assert len(windows) == 1
    assert windows[0].start == START + timedelta(days=60)
    assert windows[0].severity(3.5) == "high"


def test_detector_is_quiet_on_clean_and_flat_series() -> None:
    assert detect(_weekly(90).to_numpy(), START) == []
    assert detect(np.zeros(60), START) == []


def test_detector_quality_on_labeled_synthetic_data() -> None:
    medium = evaluation.evaluate_detector(range(60), "medium")
    assert medium["precision"] >= 0.9
    assert medium["recall"] >= 0.9
    assert medium["mean_detection_latency_days"] <= 1
    low = evaluation.evaluate_detector(range(60), "low")
    high = evaluation.evaluate_detector(range(60), "high")
    assert low["false_positives"] <= medium["false_positives"] <= high["false_positives"]
    assert low["recall"] <= medium["recall"] <= high["recall"]


def test_evaluation_is_reproducible() -> None:
    assert evaluation.evaluate_detector(range(10)) == evaluation.evaluate_detector(range(10))
    first = evaluation.make_series(3)
    np.testing.assert_array_equal(first.values, evaluation.make_series(3).values)
