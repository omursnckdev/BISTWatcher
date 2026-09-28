"""Volume indicators: volume MA / ratio, OBV, Accumulation/Distribution line, turnover."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bist_quant.indicators._smoothing import sma


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.DataFrame:
    """``volume_ma`` and ``volume_ratio = volume / volume_ma``.

    The average includes the current bar (known at the close), per the spec's
    ``CurrentVolume / AverageVolume20`` definition.
    """
    ma = sma(volume, period)
    return pd.DataFrame(
        {"volume_ma": ma, "volume_ratio": volume / ma.replace(0, np.nan)}, index=volume.index
    )


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum().rename("obv")


def accumulation_distribution(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series
) -> pd.Series:
    """Chaikin Accumulation/Distribution line."""
    rng = (high - low).replace(0, np.nan)
    mfm = (((close - low) - (high - close)) / rng).fillna(0.0)
    return (mfm * volume).cumsum().rename("ad_line")


def average_turnover(close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
    """Average daily traded value (price * volume), in the quote currency."""
    return sma(close * volume, period).rename("avg_turnover")
