"""Trend indicators: EMA and ADX / DMI."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bist_quant.indicators._smoothing import ema, rma
from bist_quant.indicators.volatility import true_range

__all__ = ["ema", "adx"]


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    """Wilder's ADX with +DI / -DI.

    Returns columns ``adx``, ``plus_di``, ``minus_di``.
    """
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )
    # First bar has no previous bar -> no directional movement defined.
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    tr = true_range(high, low, close)
    tr.iloc[0] = np.nan

    atr_ = rma(tr, period)
    plus_di = 100 * rma(plus_dm, period) / atr_
    minus_di = 100 * rma(minus_dm, period) / atr_
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    return pd.DataFrame(
        {"adx": rma(dx, period), "plus_di": plus_di, "minus_di": minus_di}, index=close.index
    )
