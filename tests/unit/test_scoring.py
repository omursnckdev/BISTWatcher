from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from bist_quant.config import Weights
from bist_quant.models.signals import ComponentScore
from bist_quant.scoring.composite_score import composite_score, score_components
from bist_quant.scoring.technical_score import (
    momentum_score,
    relative_strength_score,
    trend_score,
    volatility_score,
    volume_score,
)

BULL_ROW = {
    "close": 110.0,
    "prev_close": 107.0,
    "ema_fast": 106.0,
    "ema_medium": 100.0,
    "ema_long": 95.0,
    "ema_slow": 90.0,
    "adx": 32.0,
    "plus_di": 30.0,
    "minus_di": 12.0,
    "trend_persistence": 1.0,
    "rsi": 58.0,
    "macd": 1.5,
    "macd_signal": 1.0,
    "macd_hist": 0.5,
    "macd_hist_prev": 0.3,
    "roc": 4.0,
    "rsi_above50_frac": 1.0,
    "volume_ratio": 2.2,
    "obv": 1e6,
    "obv_ema": 8e5,
    "ad_slope": 1e5,
    "bb_upper": 109.0,
    "atr": 2.5,
    "atr_pct": 2.3,
    "bb_width_pctile": 0.1,
    "rs_5d": 3.0,
    "rs_20d": 6.0,
    "rs_60d": 12.0,
}
BEAR_ROW = {
    "close": 80.0,
    "prev_close": 84.0,
    "ema_fast": 86.0,
    "ema_medium": 92.0,
    "ema_long": 96.0,
    "ema_slow": 100.0,
    "adx": 30.0,
    "plus_di": 10.0,
    "minus_di": 28.0,
    "trend_persistence": 0.0,
    "rsi": 25.0,
    "macd": -1.5,
    "macd_signal": -1.0,
    "macd_hist": -0.5,
    "macd_hist_prev": -0.3,
    "roc": -6.0,
    "rsi_above50_frac": 0.0,
    "volume_ratio": 2.0,
    "obv": 5e5,
    "obv_ema": 8e5,
    "ad_slope": -1e5,
    "bb_upper": 95.0,
    "atr": 4.0,
    "atr_pct": 7.0,
    "bb_width_pctile": 0.9,
    "rs_5d": -4.0,
    "rs_20d": -8.0,
    "rs_60d": -15.0,
}


def _all(row, settings):
    w, r, i = settings.scoring.weights, settings.scoring.rules, settings.indicators
    return [
        trend_score(row, w.trend, r, i),
        momentum_score(row, w.momentum, r),
        volume_score(row, w.volume, r),
        relative_strength_score(row, w.relative_strength, i),
        volatility_score(row, w.volatility, r, i),
    ]


def test_bullish_row_scores_maximum(settings):
    for comp in _all(BULL_ROW, settings):
        assert comp.points == pytest.approx(comp.max_points), (comp.name, comp.negative_factors)
        assert comp.positive_factors


def test_bearish_row_scores_low_with_reasons(settings):
    for comp in _all(BEAR_ROW, settings):
        assert comp.ratio < 0.25, comp.name
        assert comp.negative_factors, comp.name


def test_distribution_day_is_flagged(settings):
    comp = volume_score(BEAR_ROW, 10, settings.scoring.rules)
    assert any("distribution" in r for r in comp.negative_factors)


def test_component_bounds_fuzz(settings):
    rng = np.random.default_rng(0)
    keys = list(BULL_ROW)
    for _ in range(300):
        row = {k: float(rng.normal(BULL_ROW[k], abs(BULL_ROW[k]) + 1)) for k in keys}
        row["trend_persistence"] = float(rng.uniform())
        row["rsi_above50_frac"] = float(rng.uniform())
        row["rsi"] = float(rng.uniform(0, 100))
        if rng.uniform() < 0.2:
            row[keys[rng.integers(len(keys))]] = float("nan")
        for comp in _all(row, settings):
            assert 0 <= comp.points <= comp.max_points + 1e-9


def test_rsi_zones(settings):
    rules = settings.scoring.rules
    pts = {v: momentum_score({"rsi": v}, 15, rules).points for v in (20, 40, 55, 68, 80)}
    assert pts[55] > pts[68] > pts[80] > pts[40] > pts[20] == 0


def test_score_components_and_composite(settings):
    regime = ComponentScore(name="market_regime", points=10, max_points=10)
    comps = score_components(BULL_ROW, settings, regime)
    assert list(comps) == [
        "trend",
        "momentum",
        "volume",
        "relative_strength",
        "news",
        "institutional_flow",
        "market_regime",
        "volatility",
    ]
    assert not comps["news"].enabled and not comps["institutional_flow"].enabled
    assert composite_score(comps, renormalize=True) == 100.0
    assert composite_score(comps, renormalize=False) == 70.0


def test_composite_renormalisation_example():
    comps = {
        "a": ComponentScore(name="a", points=56, max_points=70),
        "b": ComponentScore(name="b", points=0, max_points=30, enabled=False),
    }
    assert composite_score(comps) == 80.0
    assert composite_score(comps, renormalize=False) == 56.0


def test_extra_components_are_used(settings):
    regime = ComponentScore(name="market_regime", points=5, max_points=10)
    news = ComponentScore(name="news", points=15, max_points=15)
    comps = score_components(BULL_ROW, settings, regime, {"news": news})
    assert comps["news"].enabled and comps["news"].points == 15
    with pytest.raises(ValueError):
        score_components(BULL_ROW, settings, regime, {"bogus": news})


def test_weights_must_sum_to_100():
    with pytest.raises(ValidationError):
        Weights(trend=30)
