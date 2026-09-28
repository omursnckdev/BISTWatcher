"""Feature pipeline: turns cleaned OHLCV bars into one row of indicators per session.

Every column at date *t* is computed from bars dated <= *t* only (no look-ahead).
Signals are therefore "known at the close of T"; any execution happens at T+1.
"""

from __future__ import annotations

import pandas as pd

from bist_quant.config import IndicatorSettings, RiskSettings, ScoringRules
from bist_quant.indicators._smoothing import ema
from bist_quant.indicators.momentum import macd, roc, rsi
from bist_quant.indicators.relative_strength import relative_strength
from bist_quant.indicators.trend import adx
from bist_quant.indicators.volatility import (
    atr,
    bollinger_bands,
    historical_volatility,
    rolling_percentile_rank,
)
from bist_quant.indicators.volume import (
    accumulation_distribution,
    average_turnover,
    obv,
    volume_ratio,
)

# Bars dropped from the resistance window: the current thrust is not "overhead supply".
RESISTANCE_SKIP_BARS = 5


def compute_features(
    bars: pd.DataFrame,
    ind: IndicatorSettings,
    rules: ScoringRules,
    risk: RiskSettings,
    benchmark_close: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute all Phase 1 technical features for one symbol.

    ``bars`` must be cleaned (and, if desired, adjusted) OHLCV indexed by date.
    ``raw_close`` (unadjusted close) is passed through when present.
    """
    high, low, close, volume = bars["high"], bars["low"], bars["close"], bars["volume"]
    f = pd.DataFrame(index=bars.index)
    f["open"], f["high"], f["low"], f["close"], f["volume"] = (
        bars["open"],
        high,
        low,
        close,
        volume,
    )
    f["raw_close"] = bars.get("raw_close", close)
    f["prev_close"] = close.shift(1)

    # Trend
    f["ema_fast"] = ema(close, ind.ema_fast)
    f["ema_medium"] = ema(close, ind.ema_medium)
    f["ema_long"] = ema(close, ind.ema_long)
    f["ema_slow"] = ema(close, ind.ema_slow)
    f = f.join(adx(high, low, close, ind.adx_period))
    above_medium = (close > f["ema_medium"]).astype(float).where(f["ema_medium"].notna())
    f["trend_persistence"] = above_medium.rolling(rules.trend_persistence_window).mean()

    # Momentum
    f["rsi"] = rsi(close, ind.rsi_period)
    f = f.join(macd(close, ind.macd_fast, ind.macd_slow, ind.macd_signal))
    f["macd_hist_prev"] = f["macd_hist"].shift(1)
    f["roc"] = roc(close, ind.roc_period)
    rsi_above_50 = (f["rsi"] > 50).astype(float).where(f["rsi"].notna())
    f["rsi_above50_frac"] = rsi_above_50.rolling(rules.momentum_persistence_window).mean()

    # Volatility
    f = f.join(bollinger_bands(close, ind.bb_period, ind.bb_std))
    f["bb_width_pctile"] = rolling_percentile_rank(f["bb_width"], rules.squeeze_lookback)
    f["atr"] = atr(high, low, close, ind.atr_period)
    f["atr_pct"] = f["atr"] / close * 100
    f["hv20"] = historical_volatility(close, 20)

    # Volume
    f = f.join(volume_ratio(volume, ind.volume_ma_period))
    f["obv"] = obv(close, volume)
    f["obv_ema"] = ema(f["obv"], rules.obv_ema_period)
    f["ad_line"] = accumulation_distribution(high, low, close, volume)
    f["ad_slope"] = f["ad_line"] - f["ad_line"].shift(rules.ad_slope_window)
    # Turnover uses the unadjusted close: it is a real TRY liquidity measure.
    f["avg_turnover"] = average_turnover(f["raw_close"], volume, ind.volume_ma_period)
    f["avg_volume"] = f["volume_ma"]

    # Relative strength vs benchmark
    if benchmark_close is not None:
        f = f.join(relative_strength(close, benchmark_close, ind.rs_windows))
    else:
        for n in ind.rs_windows:
            f[f"ret_{n}d"] = (close / close.shift(n) - 1) * 100
            f[f"rs_{n}d"] = float("nan")

    # Structure: overhead resistance for reward capping
    f["resistance"] = (
        high.shift(RESISTANCE_SKIP_BARS).rolling(risk.resistance_lookback, min_periods=20).max()
    )
    return f


def warmup_bars(ind: IndicatorSettings) -> int:
    """Bars needed before every indicator is defined (EMA-slow dominates)."""
    needs = [ind.ema_slow, ind.macd_slow + ind.macd_signal, 2 * ind.adx_period, *ind.rs_windows]
    return max(needs) + 1
