"""Look-ahead bias guard: features at date t must not change when future bars are removed."""

from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pandas as pd
import pytest

from bist_quant.data.market_data import SyntheticProvider
from bist_quant.indicators.features import compute_features
from bist_quant.regime.market_regime import compute_index_features
from bist_quant.scanner import run_scan

from ..conftest import make_bars


@pytest.mark.parametrize("cut", [260, 300, 350])
def test_features_are_causal(settings, cut):
    bars = make_bars(n=400, seed=3)
    bench = make_bars(n=400, seed=4)["close"]
    args = (settings.indicators, settings.scoring.rules, settings.risk)
    full = compute_features(bars, *args, benchmark_close=bench)
    part = compute_features(bars.iloc[:cut], *args, benchmark_close=bench.iloc[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], part, check_exact=False, rtol=1e-9)


def test_features_ignore_future_benchmark_bars(settings):
    bars = make_bars(n=320, seed=5)
    bench = make_bars(n=320, seed=6)["close"]
    args = (settings.indicators, settings.scoring.rules, settings.risk)
    base = compute_features(bars.iloc[:300], *args, benchmark_close=bench.iloc[:300])
    with_future = compute_features(bars.iloc[:300], *args, benchmark_close=bench)
    pd.testing.assert_frame_equal(base, with_future)


def test_index_features_are_causal(settings):
    bars = make_bars(n=500, seed=8)
    full = compute_index_features(bars, settings.indicators, settings.market_regime)
    part = compute_index_features(bars.iloc[:400], settings.indicators, settings.market_regime)
    pd.testing.assert_frame_equal(full.iloc[:400], part, check_exact=False, rtol=1e-9)


def test_as_of_scan_equals_scan_on_truncated_data(settings):
    """Replaying a past date must give the same result as if the future never existed."""
    as_of = date(2024, 6, 28)
    symbols = ["AAAA", "BBBB", "CCCC"]

    class Truncated(SyntheticProvider):
        async def get_daily_bars(self, symbol, start, end):
            return await super().get_daily_bars(symbol, start, min(end, as_of))

    s = settings.model_copy(deep=True)
    s.market_regime.min_breadth_symbols = 1
    a = asyncio.run(run_scan(s, symbols, SyntheticProvider(), as_of=as_of))
    b = asyncio.run(run_scan(s, symbols, Truncated(), as_of=as_of))
    assert a.as_of == b.as_of == as_of
    assert [(x.symbol, x.score, x.signal) for x in a.signals] == [
        (x.symbol, x.score, x.signal) for x in b.signals
    ]
    assert np.isfinite([x.score for x in a.signals]).all()
