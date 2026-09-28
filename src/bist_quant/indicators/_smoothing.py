"""Shared smoothing primitives (SMA-seeded recursive averages, as in TA-Lib / Wilder)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def seeded_ewm(series: pd.Series, period: int, alpha: float) -> pd.Series:
    """Recursive average ``y_t = alpha * x_t + (1 - alpha) * y_{t-1}``.

    The recursion is seeded with the simple mean of the first ``period`` valid
    values; earlier outputs are NaN. Leading NaNs in ``series`` are skipped (so it
    can be chained, e.g. MACD signal line or ADX).
    """
    values = series.to_numpy(dtype="float64")
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < period:
        return pd.Series(out, index=series.index)
    start = valid[0]
    seed_end = start + period - 1
    if np.isnan(values[start : seed_end + 1]).any():
        # Interior gaps inside the seed window: fall back to first fully valid window.
        window = pd.Series(values).rolling(period).mean()
        seed_end = int(window.first_valid_index())
    out[seed_end] = values[seed_end - period + 1 : seed_end + 1].mean()
    prev = out[seed_end]
    for i in range(seed_end + 1, len(values)):
        x = values[i]
        prev = prev if np.isnan(x) else alpha * x + (1 - alpha) * prev
        out[i] = prev
    return pd.Series(out, index=series.index)


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, alpha = 2 / (period + 1), SMA-seeded."""
    return seeded_ewm(series, period, 2.0 / (period + 1))


def rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's moving average, alpha = 1 / period, SMA-seeded."""
    return seeded_ewm(series, period, 1.0 / period)


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()
