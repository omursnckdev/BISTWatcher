"""Momentum indicators: RSI, MACD, ROC."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bist_quant.indicators._smoothing import ema, rma


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (0-100)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = rma(gain, period)
    avg_loss = rma(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # No losses in the window -> RSI = 100 (or 50 if the price did not move at all).
    flat = (avg_loss == 0) & avg_gain.notna()
    out = out.where(~flat, np.where(avg_gain > 0, 100.0, 50.0))
    return out.rename("rsi")


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line, signal line and histogram."""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame(
        {"macd": line, "macd_signal": sig, "macd_hist": line - sig}, index=close.index
    )


def roc(close: pd.Series, period: int = 10) -> pd.Series:
    """Rate of change in percent."""
    return (close / close.shift(period) - 1) * 100
