"""Suitability of a stock for very short holding periods.

Two 0-100 scores computed from the last completed sessions, for sorting the scan table:

* ``intraday``: buy and sell within the same session. It needs deep liquidity (to get in
  and out without moving the price) and a daily range wide enough to beat the round-trip
  costs, and it is easier with fresh volume and a close near the high.
* ``two_day``: hold for one or two sessions (the T+2 settlement window). It leans on
  short-term momentum and trend, with a lower liquidity bar.

These rank the *conditions* for a short trade. They are not BUY signals: the swing score
and its risk plan still decide what is a candidate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

RANGE_SESSIONS = 10  # sessions used for the average daily range
MOMENTUM_SESSIONS = 3  # sessions used for the short-term return


@dataclass(frozen=True)
class ShortTermScore:
    intraday: float
    two_day: float
    avg_range_pct: float  # average (high - low) / close over RANGE_SESSIONS, %
    return_3d_pct: float


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at ``lo``, 1 at ``hi``, clipped; NaN counts as 0."""
    if x != x or hi == lo:
        return 0.0
    return min(1.0, max(0.0, (x - lo) / (hi - lo)))


def _band(x: float, lo: float, best_lo: float, best_hi: float, hi: float) -> float:
    """1 inside [best_lo, best_hi], falling linearly to 0 at ``lo`` and ``hi``."""
    if x != x:
        return 0.0
    if x < best_lo:
        return _ramp(x, lo, best_lo)
    if x > best_hi:
        return 1.0 - _ramp(x, best_hi, hi)
    return 1.0


def _log_ramp(x: float, lo: float, hi: float) -> float:
    if x != x or x <= 0:
        return 0.0
    return _ramp(math.log10(x), math.log10(lo), math.log10(hi))


def short_term_score(f: pd.DataFrame | None) -> ShortTermScore | None:
    """Scores from a feature frame (needs high, low, close, avg_turnover, ...)."""
    if f is None or len(f) < RANGE_SESSIONS + 1:
        return None
    last = f.iloc[-1]
    recent = f.iloc[-RANGE_SESSIONS:]
    rng = float(((recent["high"] - recent["low"]) / recent["close"]).mean() * 100)
    ret3 = float((f["close"].iloc[-1] / f["close"].iloc[-1 - MOMENTUM_SESSIONS] - 1) * 100)
    turnover = float(last.get("avg_turnover", float("nan")))
    vol_ratio = float(last.get("volume_ratio", float("nan")))
    span = float(last["high"] - last["low"])
    close_loc = float((last["close"] - last["low"]) / span) if span > 0 else 0.5
    atr_pct = float(last.get("atr_pct", float("nan")))
    rsi = float(last.get("rsi", float("nan")))
    above_ema = float(last["close"] > last.get("ema_fast", float("inf")))
    hist = f["macd_hist"].iloc[-2:] if "macd_hist" in f else pd.Series(dtype=float)
    hist_rising = float(len(hist) == 2 and hist.iloc[-1] > hist.iloc[-2])

    intraday = 100 * (
        0.40 * _log_ramp(turnover, 50e6, 1e9)  # 50M TL/day -> 0, 1B TL/day -> full
        + 0.35 * _band(rng, 1.0, 3.0, 6.0, 12.0)  # needs room above ~0.4% round-trip cost
        + 0.15 * _ramp(vol_ratio, 0.8, 1.8)
        + 0.10 * close_loc
    )
    two_day = 100 * (
        0.20 * _log_ramp(turnover, 30e6, 500e6)
        + 0.25 * _ramp(ret3, -1.0, 6.0)
        + 0.10 * above_ema
        + 0.10 * _band(rsi, 40, 52, 68, 80)
        + 0.10 * hist_rising
        + 0.15 * _band(atr_pct, 1.0, 2.5, 5.5, 9.0)
        + 0.10 * _ramp(vol_ratio, 0.8, 1.8)
    )
    return ShortTermScore(round(intraday, 1), round(two_day, 1), round(rng, 2), round(ret3, 2))
