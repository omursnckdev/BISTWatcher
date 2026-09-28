"""Slow, loop-based reference implementations used to verify the vectorised indicators."""

from __future__ import annotations

import math


def ref_ema(values: list[float], period: int) -> list[float]:
    out = [math.nan] * len(values)
    if len(values) < period:
        return out
    alpha = 2 / (period + 1)
    out[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def ref_wilder(values: list[float], period: int, first: int = 0) -> list[float]:
    """Wilder smoothing starting at index ``first`` (seed = SMA of values[first:first+period])."""
    out = [math.nan] * len(values)
    seed_end = first + period - 1
    if seed_end >= len(values):
        return out
    out[seed_end] = sum(values[first : seed_end + 1]) / period
    for i in range(seed_end + 1, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def ref_rsi(closes: list[float], period: int = 14) -> list[float]:
    gains = [math.nan] + [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [math.nan] + [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag = ref_wilder(gains, period, first=1)
    al = ref_wilder(losses, period, first=1)
    out = []
    for g, lo in zip(ag, al, strict=True):
        if math.isnan(g):
            out.append(math.nan)
        elif lo == 0:
            out.append(100.0 if g > 0 else 50.0)
        else:
            out.append(100 - 100 / (1 + g / lo))
    return out


def ref_true_range(h: list[float], lo: list[float], c: list[float]) -> list[float]:
    tr = [h[0] - lo[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    return tr


def ref_atr(h, lo, c, period: int = 14) -> list[float]:
    return ref_wilder(ref_true_range(h, lo, c), period)


def ref_adx(h, lo, c, period: int = 14) -> tuple[list[float], list[float], list[float]]:
    n = len(c)
    pdm, mdm = [math.nan], [math.nan]
    for i in range(1, n):
        up, down = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
    tr = [math.nan] + ref_true_range(h, lo, c)[1:]
    atr = ref_wilder(tr, period, first=1)
    sp, sm = ref_wilder(pdm, period, first=1), ref_wilder(mdm, period, first=1)
    pdi = [100 * a / b if not math.isnan(a) else math.nan for a, b in zip(sp, atr, strict=True)]
    mdi = [100 * a / b if not math.isnan(a) else math.nan for a, b in zip(sm, atr, strict=True)]
    dx = []
    for p, m in zip(pdi, mdi, strict=True):
        if math.isnan(p):
            dx.append(math.nan)
        else:
            dx.append(100 * abs(p - m) / (p + m) if p + m else math.nan)
    first_dx = next(i for i, v in enumerate(dx) if not math.isnan(v))
    adx = ref_wilder(dx, period, first=first_dx)
    return adx, pdi, mdi
