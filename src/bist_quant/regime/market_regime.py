"""Market regime classifier (XU100 by default).

Rules (evaluated in this order, first match wins):

* ``BEAR``            close < EMA50 and EMA50 < EMA200
* ``HIGH_VOLATILITY`` ATR% in the top decile of its 1-year history *and* at least
                      ``high_vol_min_ratio`` x its 1-year median (a percentile alone
                      fires in calm markets), or the index is more than
                      ``high_vol_drawdown_pct`` below its 60-session high
* ``BULL``            close > EMA50, EMA50 > EMA200 and MACD line > 0
* ``NEUTRAL``         everything else

The regime changes the BUY threshold (see ``strategy.buy_threshold``) and feeds
the 0-10 market-regime score component.
"""

from __future__ import annotations

import math

import pandas as pd

from bist_quant.config import IndicatorSettings, MarketRegimeSettings
from bist_quant.indicators._smoothing import ema
from bist_quant.indicators.momentum import macd
from bist_quant.indicators.volatility import atr, rolling_percentile_rank
from bist_quant.models.signals import ComponentScore, MarketRegime, RegimeSnapshot

# Raw points for the regime score; scaled to the configured weight.
_REGIME_POINTS = {
    "above_fast": 2.0,
    "above_medium": 2.0,
    "above_slow": 2.0,
    "medium_above_slow": 2.0,
    "macd_positive": 1.0,
    "breadth": 1.0,
}


def compute_index_features(
    bars: pd.DataFrame, ind: IndicatorSettings, cfg: MarketRegimeSettings
) -> pd.DataFrame:
    close = bars["close"]
    f = pd.DataFrame(index=bars.index)
    f["close"] = close
    f["ema_fast"] = ema(close, ind.ema_fast)
    f["ema_medium"] = ema(close, ind.ema_medium)
    f["ema_slow"] = ema(close, ind.ema_slow)
    f = f.join(macd(close, ind.macd_fast, ind.macd_slow, ind.macd_signal))
    f["atr_pct"] = atr(bars["high"], bars["low"], close, ind.atr_period) / close * 100
    f["atr_pct_pctile"] = rolling_percentile_rank(f["atr_pct"], cfg.high_vol_lookback)
    median = f["atr_pct"].rolling(cfg.high_vol_lookback, min_periods=20).median()
    f["atr_pct_vs_median"] = f["atr_pct"] / median
    rolling_high = close.rolling(cfg.drawdown_lookback, min_periods=1).max()
    f["drawdown_pct"] = (1 - close / rolling_high) * 100
    return f


def _ok(x: float | None) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def classify_row(row: pd.Series, cfg: MarketRegimeSettings) -> tuple[MarketRegime, list[str]]:
    close, e50, e200 = row["close"], row["ema_medium"], row["ema_slow"]
    reasons: list[str] = []
    if close < e50 and e50 < e200:
        reasons.append("index below EMA50 and EMA50 below EMA200")
        return MarketRegime.BEAR, reasons
    pctile, dd = row.get("atr_pct_pctile"), row.get("drawdown_pct")
    ratio = row.get("atr_pct_vs_median")
    if (
        _ok(pctile)
        and _ok(ratio)
        and pctile >= cfg.high_vol_percentile
        and ratio >= cfg.high_vol_min_ratio
    ):
        reasons.append(
            f"index ATR% at {pctile:.0%} percentile of {cfg.high_vol_lookback}D range "
            f"({ratio:.2f}x median)"
        )
        return MarketRegime.HIGH_VOLATILITY, reasons
    if _ok(dd) and dd >= cfg.high_vol_drawdown_pct:
        reasons.append(f"index {dd:.1f}% below its {cfg.drawdown_lookback}D high")
        return MarketRegime.HIGH_VOLATILITY, reasons
    if close > e50 and e50 > e200 and row["macd"] > 0:
        reasons.append("index above EMA50, EMA50 above EMA200, MACD positive")
        return MarketRegime.BULL, reasons
    reasons.append("mixed index trend signals")
    return MarketRegime.NEUTRAL, reasons


def regime_score(
    row: pd.Series, weight: float, breadth_pct: float | None, cfg: MarketRegimeSettings
) -> ComponentScore:
    pos: list[str] = []
    neg: list[str] = []
    earned = 0.0
    available = 0.0

    def check(key: str, cond: bool, good: str, bad: str) -> None:
        nonlocal earned, available
        available += _REGIME_POINTS[key]
        if cond:
            earned += _REGIME_POINTS[key]
            pos.append(good)
        else:
            neg.append(bad)

    check("above_fast", row["close"] > row["ema_fast"], "Index above EMA20", "Index below EMA20")
    check(
        "above_medium", row["close"] > row["ema_medium"], "Index above EMA50", "Index below EMA50"
    )
    check("above_slow", row["close"] > row["ema_slow"], "Index above EMA200", "Index below EMA200")
    check(
        "medium_above_slow",
        row["ema_medium"] > row["ema_slow"],
        "Index EMA50 above EMA200",
        "Index EMA50 below EMA200",
    )
    check("macd_positive", row["macd"] > 0, "Index MACD positive", "Index MACD negative")
    if _ok(breadth_pct):
        check(
            "breadth",
            breadth_pct >= cfg.breadth_healthy_pct,
            f"Breadth healthy: {breadth_pct:.0f}% of universe above EMA50",
            f"Weak breadth: {breadth_pct:.0f}% of universe above EMA50",
        )
    points = weight * earned / available if available else 0.0
    return ComponentScore(
        name="market_regime",
        points=points,
        max_points=weight,
        positive_factors=pos,
        negative_factors=neg,
    )


def classify_market(
    index_features: pd.DataFrame,
    cfg: MarketRegimeSettings,
    weight: float,
    breadth_pct: float | None = None,
    as_of: pd.Timestamp | None = None,
) -> RegimeSnapshot:
    """Regime snapshot on ``as_of`` (default: latest fully-defined index bar)."""
    valid = index_features.dropna(subset=["ema_slow", "macd"])
    if as_of is not None:
        valid = valid.loc[:as_of]
    if valid.empty:
        raise ValueError("not enough index history to classify the market regime")
    row = valid.iloc[-1]
    regime, reasons = classify_row(row, cfg)
    return RegimeSnapshot(
        index=cfg.index,
        date=valid.index[-1].date(),
        regime=regime,
        score=regime_score(row, weight, breadth_pct, cfg),
        close=float(row["close"]),
        ema50=float(row["ema_medium"]),
        ema200=float(row["ema_slow"]),
        atr_pct_percentile=None if not _ok(row["atr_pct_pctile"]) else float(row["atr_pct_pctile"]),
        drawdown_pct=float(row["drawdown_pct"]),
        breadth_pct=breadth_pct,
        reasons=reasons,
    )


def regime_history(index_features: pd.DataFrame, cfg: MarketRegimeSettings) -> pd.Series:
    """Regime label for every fully-defined index bar (for research / backtests)."""
    valid = index_features.dropna(subset=["ema_slow", "macd"])
    return valid.apply(lambda r: classify_row(r, cfg)[0].value, axis=1).rename("market_regime")
