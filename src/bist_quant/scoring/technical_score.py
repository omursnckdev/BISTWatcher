"""Technical factor scores: trend, momentum, volume, relative strength, volatility.

Each scorer awards *raw* points from a fixed rubric (documented inline), then
scales the result to the configured factor weight. Every point awarded or
withheld is recorded as a human-readable reason.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from bist_quant.config import IndicatorSettings, ScoringRules
from bist_quant.models.signals import ComponentScore


def _v(row: Mapping, key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at ``lo``, 1 at ``hi``, linear in between (avoids knife-edge thresholds)."""
    if hi == lo:
        return float(x >= hi)
    return min(max((x - lo) / (hi - lo), 0.0), 1.0)


class _Rubric:
    def __init__(self, name: str, raw_max: float, weight: float) -> None:
        self.name, self.raw_max, self.weight = name, raw_max, weight
        self.raw = 0.0
        self.pos: list[str] = []
        self.neg: list[str] = []

    def award(self, points: float, reason: str | None = None) -> None:
        self.raw += points
        if reason:
            self.pos.append(reason)

    def miss(self, reason: str) -> None:
        self.neg.append(reason)

    def check(self, cond: bool | None, points: float, good: str, bad: str) -> None:
        if cond:
            self.award(points, good)
        else:
            self.miss(bad if cond is not None else f"{bad} (insufficient data)")

    def result(self) -> ComponentScore:
        raw = min(max(self.raw, 0.0), self.raw_max)
        return ComponentScore(
            name=self.name,
            points=self.weight * raw / self.raw_max,
            max_points=self.weight,
            positive_factors=self.pos,
            negative_factors=self.neg,
        )


def _gt(a: float | None, b: float | None) -> bool | None:
    return None if a is None or b is None else a > b


def trend_score(
    row: Mapping, weight: float, rules: ScoringRules, ind: IndicatorSettings
) -> ComponentScore:
    """Raw rubric (20): close>EMAfast 2, close>EMAmed 3, close>EMAslow 3, EMAfast>EMAmed 2,
    EMAmed>EMAslow 3, ADX trend strength (with +DI>-DI) 2, trend persistence 5."""
    r = _Rubric("trend", 20, weight)
    close = _v(row, "close")
    ef, em, es = _v(row, "ema_fast"), _v(row, "ema_medium"), _v(row, "ema_slow")
    f, m, s = ind.ema_fast, ind.ema_medium, ind.ema_slow
    r.check(_gt(close, ef), 2, f"Price above EMA{f}", f"Price below EMA{f}")
    r.check(_gt(close, em), 3, f"Price above EMA{m}", f"Price below EMA{m}")
    r.check(_gt(close, es), 3, f"Price above EMA{s}", f"Price below EMA{s}")
    r.check(_gt(ef, em), 2, f"EMA{f} above EMA{m}", f"EMA{f} below EMA{m}")
    r.check(_gt(em, es), 3, f"EMA{m} above EMA{s}", f"EMA{m} below EMA{s}")

    adx_v, pdi, mdi = _v(row, "adx"), _v(row, "plus_di"), _v(row, "minus_di")
    if adx_v is None or pdi is None or mdi is None:
        r.miss("ADX unavailable (insufficient data)")
    elif pdi <= mdi:
        r.miss(f"ADX {adx_v:.0f} with -DI dominant (directional pressure is down)")
    elif adx_v >= rules.adx_strong:
        r.award(2, f"Strong uptrend: ADX {adx_v:.0f}, +DI > -DI")
    elif adx_v >= rules.adx_emerging:
        r.award(1, f"Emerging uptrend: ADX {adx_v:.0f}")
    else:
        r.miss(f"Weak trend / sideways: ADX {adx_v:.0f}")

    pers = _v(row, "trend_persistence")
    if pers is None:
        r.miss("Trend persistence unavailable")
    else:
        r.award(5 * pers)
        window = rules.trend_persistence_window
        label = f"Closed above EMA{m} on {pers:.0%} of last {window} sessions"
        (r.pos if pers >= 0.6 else r.neg).append(label)
    return r.result()


def momentum_score(row: Mapping, weight: float, rules: ScoringRules) -> ComponentScore:
    """Raw rubric (15): RSI zone 0-5, MACD>signal 3, histogram rising 2, histogram>0 1,
    ROC>0 2, RSI>50 persistence 0-2."""
    r = _Rubric("momentum", 15, weight)
    rsi_v = _v(row, "rsi")
    lo_h, hi_h = rules.rsi_healthy
    lo_s, hi_s = rules.rsi_strong
    if rsi_v is None:
        r.miss("RSI unavailable")
    elif lo_h <= rsi_v < hi_h:
        r.award(5, f"RSI healthy at {rsi_v:.0f}")
    elif lo_s <= rsi_v <= hi_s:
        r.award(4, f"RSI strong at {rsi_v:.0f}")
    elif rsi_v > rules.rsi_overbought:
        r.award(2)
        r.miss(f"RSI {rsi_v:.0f}: potentially overextended")
    elif rsi_v >= rules.rsi_oversold:
        r.award(1)
        r.miss(f"RSI weak at {rsi_v:.0f}")
    else:
        r.miss(f"RSI oversold at {rsi_v:.0f} (no trend confirmation)")

    macd_v, sig, hist, hist_prev = (
        _v(row, "macd"),
        _v(row, "macd_signal"),
        _v(row, "macd_hist"),
        _v(row, "macd_hist_prev"),
    )
    r.check(_gt(macd_v, sig), 3, "MACD above signal line", "MACD below signal line")
    r.check(_gt(hist, hist_prev), 2, "MACD histogram rising", "MACD histogram falling")
    if hist is not None and hist > 0:
        crossed = hist_prev is not None and hist_prev <= 0
        r.award(1, "MACD histogram crossed above zero" if crossed else "MACD histogram positive")
    else:
        r.miss("MACD histogram negative")
    roc_v = _v(row, "roc")
    r.check(
        None if roc_v is None else roc_v > 0,
        2,
        f"Positive rate of change ({roc_v:+.1f}%)" if roc_v is not None else "",
        f"Negative rate of change ({roc_v:+.1f}%)" if roc_v is not None else "Rate of change",
    )
    frac = _v(row, "rsi_above50_frac")
    if frac is not None:
        r.award(2 * frac)
        window = rules.momentum_persistence_window
        if frac >= 0.7:
            r.pos.append(f"RSI above 50 on {frac:.0%} of last {window} sessions")
        elif frac <= 0.3:
            r.neg.append(f"RSI above 50 on only {frac:.0%} of last {window} sessions")
    return r.result()


def volume_score(row: Mapping, weight: float, rules: ScoringRules) -> ComponentScore:
    """Raw rubric (10): volume confirmation of the day's direction 0-4, OBV above its EMA 2,
    A/D line rising 2, Bollinger breakout on volume 2."""
    r = _Rubric("volume", 10, weight)
    ratio, close, prev = _v(row, "volume_ratio"), _v(row, "close"), _v(row, "prev_close")
    l1, l2, l3 = rules.volume_ratio_levels
    if ratio is None or close is None or prev is None:
        r.miss("Volume ratio unavailable")
    elif close > prev:
        if ratio >= l3:
            r.award(4, f"Up day on {ratio:.1f}x average volume")
        elif ratio >= l2:
            r.award(3, f"Up day on {ratio:.1f}x average volume")
        elif ratio >= l1:
            r.award(2, f"Up day on {ratio:.1f}x average volume")
        elif ratio >= 0.8:
            r.award(1)
            r.miss(f"Up day on unremarkable volume ({ratio:.1f}x)")
        else:
            r.miss(f"Up day on thin volume ({ratio:.1f}x)")
    elif ratio >= rules.distribution_volume_ratio:
        r.miss(f"Down day on {ratio:.1f}x average volume (distribution risk)")
    elif ratio < 0.8:
        r.award(2, f"Pullback on light volume ({ratio:.1f}x)")
    else:
        r.award(1)
        r.miss(f"Down day on {ratio:.1f}x average volume")

    obv_v, obv_e = _v(row, "obv"), _v(row, "obv_ema")
    r.check(_gt(obv_v, obv_e), 2, "OBV above its average (accumulation)", "OBV below its average")
    ad = _v(row, "ad_slope")
    r.check(
        None if ad is None else ad > 0,
        2,
        f"Accumulation/Distribution line rising over {rules.ad_slope_window} sessions",
        f"Accumulation/Distribution line falling over {rules.ad_slope_window} sessions",
    )
    upper = _v(row, "bb_upper")
    if close is not None and upper is not None and ratio is not None:
        if close > upper and ratio >= l1:
            r.award(2, "Close above upper Bollinger Band on above-average volume")
        elif close > upper:
            r.miss("Bollinger breakout without volume confirmation")
    return r.result()


def relative_strength_score(
    row: Mapping, weight: float, ind: IndicatorSettings, benchmark: str = "XU100"
) -> ComponentScore:
    """Raw rubric (10): short-window RS 2, medium 4, long 4. Each ramps linearly from 0 at
    -k to full at +k percentage points, k = 2 * sqrt(window / 20) (so 1pp for 5D)."""
    r = _Rubric("relative_strength", 10, weight)
    for window, pts in zip(ind.rs_windows, (2, 4, 4), strict=True):
        rs = _v(row, f"rs_{window}d")
        if rs is None:
            r.miss(f"{window}D relative strength unavailable")
            continue
        k = 2 * math.sqrt(window / 20)
        r.award(pts * _ramp(rs, -k, k))
        text = f"{window}D return {rs:+.1f}pp vs {benchmark}"
        (r.pos if rs > 0 else r.neg).append(("Outperforming: " if rs > 0 else "Lagging: ") + text)
    return r.result()


def volatility_score(
    row: Mapping, weight: float, rules: ScoringRules, ind: IndicatorSettings
) -> ComponentScore:
    """Raw rubric (5): ATR% in a tradable band 2, extension from EMAfast 0-2,
    Bollinger squeeze (volatility compression) 1."""
    r = _Rubric("volatility", 5, weight)
    atr_pct = _v(row, "atr_pct")
    lo, hi = rules.atr_pct_band
    if atr_pct is None:
        r.miss("ATR unavailable")
    elif lo <= atr_pct <= hi:
        r.award(2, f"ATR {atr_pct:.1f}% of price (tradable volatility)")
    elif atr_pct < lo:
        r.miss(f"ATR only {atr_pct:.1f}% of price (low reward potential)")
    else:
        r.miss(f"ATR {atr_pct:.1f}% of price (very volatile)")

    close, ef, atr_v = _v(row, "close"), _v(row, "ema_fast"), _v(row, "atr")
    if close is None or ef is None or not atr_v:
        r.miss("Extension unavailable")
    else:
        ext = (close - ef) / atr_v
        if 0 <= ext <= rules.max_extension_atr:
            r.award(2, f"Not overextended ({ext:.1f} ATR above EMA{ind.ema_fast})")
        elif -1 <= ext < 0:
            r.award(1, f"Shallow pullback to EMA{ind.ema_fast} ({ext:.1f} ATR)")
        elif ext > rules.max_extension_atr:
            r.miss(f"Overextended: {ext:.1f} ATR above EMA{ind.ema_fast}")
        else:
            r.miss(f"{abs(ext):.1f} ATR below EMA{ind.ema_fast}")

    pct = _v(row, "bb_width_pctile")
    if pct is not None and pct <= rules.squeeze_percentile:
        lookback = rules.squeeze_lookback
        r.award(1, f"Bollinger squeeze: band width in bottom {pct:.0%} of {lookback}D range")
    return r.result()
