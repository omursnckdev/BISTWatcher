"""Volatility indicators: Bollinger Bands, true range, ATR, historical volatility."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bist_quant.indicators._smoothing import rma, sma


def bollinger_bands(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands using the population standard deviation (ddof=0).

    Returns ``bb_mid``, ``bb_upper``, ``bb_lower``, ``bb_width`` (relative to mid)
    and ``bb_pct_b``.
    """
    mid = sma(close, period)
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width, "bb_pct_b": pct_b},
        index=close.index,
    )


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    return ranges.max(axis=1, skipna=True).rename("tr")


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Average True Range. The first TR is ``high - low``."""
    return rma(true_range(high, low, close), period).rename("atr")


def historical_volatility(close: pd.Series, window: int = 20, periods: int = 252) -> pd.Series:
    """Annualised close-to-close volatility in percent."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window, min_periods=window).std() * np.sqrt(periods) * 100


def rolling_percentile_rank(series: pd.Series, window: int) -> pd.Series:
    """Percentile rank (0-1) of the latest value inside its trailing ``window``."""

    def _rank(values: np.ndarray) -> float:
        last = values[-1]
        return float((values <= last).sum() - 1) / (len(values) - 1) if len(values) > 1 else 0.5

    return series.rolling(window, min_periods=max(20, window // 4)).apply(_rank, raw=True)
