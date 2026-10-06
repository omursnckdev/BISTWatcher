import numpy as np
import pandas as pd

from bist_quant.strategy.short_term import short_term_score


def frame(closes, rng_pct=3.0, turnover=500e6, vol_ratio=1.5, rsi=60.0, atr_pct=3.0):
    idx = pd.bdate_range("2026-01-05", periods=len(closes), name="date")
    c = np.asarray(closes, float)
    half = c * rng_pct / 200
    return pd.DataFrame(
        {
            "high": c + half,
            "low": c - half,
            "close": c + half * 0.8,  # closes near the high
            "avg_turnover": turnover,
            "volume_ratio": vol_ratio,
            "rsi": rsi,
            "atr_pct": atr_pct,
            "ema_fast": c * 0.97,
            "macd_hist": np.linspace(-0.1, 0.2, len(c)),
        },
        index=idx,
    )


def test_too_short_history():
    assert short_term_score(frame([100] * 5)) is None
    assert short_term_score(None) is None


def test_liquid_wide_range_beats_thin_quiet_stock():
    rising = list(np.linspace(100, 106, 20))
    good = short_term_score(frame(rising))
    thin = short_term_score(frame(rising, rng_pct=0.6, turnover=20e6, vol_ratio=0.7))
    assert good.intraday > 75 and thin.intraday < 25
    assert good.two_day > thin.two_day
    assert 0 <= thin.intraday <= 100 and 0 <= good.two_day <= 100
    assert good.avg_range_pct > 2.5


def test_two_day_prefers_momentum():
    up = short_term_score(frame(list(np.linspace(100, 110, 20))))
    falling = frame(list(np.linspace(110, 100, 20)), rsi=35)
    falling["ema_fast"] = falling["close"] * 1.03  # below EMA20
    falling["macd_hist"] = falling["macd_hist"][::-1].to_numpy()  # momentum fading
    down = short_term_score(falling)
    assert up.return_3d_pct > 0 > down.return_3d_pct
    assert up.two_day - down.two_day > 25


def test_extreme_range_is_penalised_for_intraday():
    calm = short_term_score(frame([100] * 20, rng_pct=4))
    wild = short_term_score(frame([100] * 20, rng_pct=14))
    assert calm.intraday > wild.intraday
