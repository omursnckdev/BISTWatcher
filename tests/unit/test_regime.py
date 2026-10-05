from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bist_quant.models.signals import MarketRegime
from bist_quant.regime.market_regime import (
    classify_market,
    classify_row,
    compute_index_features,
    regime_history,
)
from bist_quant.strategy.entry import buy_threshold


def _index(path: np.ndarray) -> pd.DataFrame:
    idx = pd.bdate_range("2022-01-03", periods=len(path), name="date")
    close = pd.Series(path, index=idx)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": 1e9,
            "adjusted_close": close,
        }
    )


def test_bull_market(settings):
    feats = compute_index_features(
        _index(np.linspace(1000, 2000, 400)), settings.indicators, settings.market_regime
    )
    snap = classify_market(feats, settings.market_regime, 10, breadth_pct=70)
    assert snap.regime is MarketRegime.BULL
    assert snap.score.points == pytest.approx(10)


def test_bear_market(settings):
    feats = compute_index_features(
        _index(np.linspace(2000, 1000, 400)), settings.indicators, settings.market_regime
    )
    snap = classify_market(feats, settings.market_regime, 10, breadth_pct=20)
    assert snap.regime is MarketRegime.BEAR
    assert snap.score.points == pytest.approx(0)


def test_high_volatility_on_crash(settings):
    path = np.concatenate([np.linspace(1000, 2000, 380), np.linspace(2000, 1650, 20)])
    feats = compute_index_features(_index(path), settings.indicators, settings.market_regime)
    snap = classify_market(feats, settings.market_regime, 10)
    assert snap.regime is MarketRegime.HIGH_VOLATILITY
    assert snap.drawdown_pct > settings.market_regime.high_vol_drawdown_pct


def test_neutral_when_mixed(settings):
    row = pd.Series(
        {
            "close": 105,
            "ema_medium": 100,
            "ema_slow": 110,
            "macd": -1.0,
            "atr_pct_pctile": 0.5,
            "atr_pct_vs_median": 1.0,
            "drawdown_pct": 3.0,
        }
    )
    assert classify_row(row, settings.market_regime)[0] is MarketRegime.NEUTRAL


def test_high_volatility_needs_elevated_atr(settings):
    base = {"close": 105, "ema_medium": 100, "ema_slow": 90, "macd": 1.0, "drawdown_pct": 2.0}
    top_but_flat = pd.Series({**base, "atr_pct_pctile": 1.0, "atr_pct_vs_median": 1.02})
    top_and_high = pd.Series({**base, "atr_pct_pctile": 0.95, "atr_pct_vs_median": 1.6})
    assert classify_row(top_but_flat, settings.market_regime)[0] is MarketRegime.BULL
    assert classify_row(top_and_high, settings.market_regime)[0] is MarketRegime.HIGH_VOLATILITY


def test_breadth_is_optional_in_score(settings):
    feats = compute_index_features(
        _index(np.linspace(1000, 2000, 400)), settings.indicators, settings.market_regime
    )
    with_breadth = classify_market(feats, settings.market_regime, 10, breadth_pct=10)
    without = classify_market(feats, settings.market_regime, 10, breadth_pct=None)
    assert without.score.points == pytest.approx(10)
    assert with_breadth.score.points == pytest.approx(9)


def test_regime_modifies_thresholds(settings):
    t = {r: buy_threshold(r, settings.strategy) for r in MarketRegime}
    assert t[MarketRegime.BULL] == 65
    assert t[MarketRegime.NEUTRAL] == 70
    assert t[MarketRegime.HIGH_VOLATILITY] == 75
    assert t[MarketRegime.BEAR] == 80
    assert t[MarketRegime.BULL] < t[MarketRegime.NEUTRAL] < t[MarketRegime.BEAR]


def test_regime_history_labels(settings):
    path = np.concatenate([np.linspace(1000, 2000, 300), np.linspace(2000, 1200, 300)])
    feats = compute_index_features(_index(path), settings.indicators, settings.market_regime)
    hist = regime_history(feats, settings.market_regime)
    assert {"BULL", "BEAR"} <= set(hist.unique())
    assert hist.iloc[-1] == "BEAR"
