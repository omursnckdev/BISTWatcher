from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from bist_quant.config import LiquiditySettings
from bist_quant.data.market_data import ISTANBUL_TZ
from bist_quant.data.quality import (
    adjust_prices,
    check_freshness,
    clean_bars,
    drop_incomplete_session,
)
from bist_quant.strategy.filters import data_filter, liquidity_filter

from ..conftest import make_bars


def test_clean_bars_drops_invalid_and_duplicates():
    bars = make_bars(n=10)
    dup = bars.iloc[[3]]
    bad = bars.iloc[[5]].copy()
    bars.loc[bars.index[7], "close"] = np.nan
    bars.loc[bars.index[8], "low"] = -1
    frame = pd.concat([bars, dup]).sort_index()
    cleaned, report = clean_bars(frame, "TEST")
    assert report.dropped_duplicate_rows == 1
    assert report.dropped_invalid_rows == 2
    assert len(cleaned) == 8
    assert cleaned.index.is_monotonic_increasing
    assert (cleaned["high"] >= cleaned[["open", "close"]].max(axis=1)).all()
    assert (cleaned["low"] <= cleaned[["open", "close"]].min(axis=1)).all()
    assert bad.index[0] in cleaned.index


def test_adjust_prices_applies_factor():
    bars = make_bars(n=5)
    bars["adjusted_close"] = bars["close"] * [0.5, 0.5, 1, 1, 1]  # e.g. 2:1 split after bar 2
    adj = adjust_prices(bars)
    assert adj["close"].iloc[0] == pytest.approx(bars["close"].iloc[0] * 0.5)
    assert adj["high"].iloc[1] == pytest.approx(bars["high"].iloc[1] * 0.5)
    assert adj["close"].iloc[-1] == pytest.approx(bars["close"].iloc[-1])
    assert (adj["raw_close"] == bars["close"]).all()


def test_drop_incomplete_session():
    bars = make_bars(n=5, start="2026-09-22")  # last bar 2026-09-28
    during = datetime(2026, 9, 28, 14, 0, tzinfo=ISTANBUL_TZ)
    after = datetime(2026, 9, 28, 19, 0, tzinfo=ISTANBUL_TZ)
    next_day = datetime(2026, 9, 29, 11, 0, tzinfo=ISTANBUL_TZ)
    assert len(drop_incomplete_session(bars, during)) == 4
    assert len(drop_incomplete_session(bars, after)) == 5
    assert len(drop_incomplete_session(bars, next_day)) == 5


def test_freshness_against_index_calendar():
    calendar = pd.bdate_range("2026-09-01", "2026-09-25")
    bars = make_bars(n=len(calendar), start="2026-09-01")
    bars = bars.drop(bars.index[[3, 10]])  # two missing sessions inside history
    _, report = clean_bars(bars, "X")
    report = check_freshness(report, bars, calendar, date(2026, 9, 26), 5, 2)
    assert report.missing_sessions == 2
    assert not report.is_stale

    suspended = bars.loc[:"2026-09-18"]
    _, rep2 = clean_bars(suspended, "Y")
    rep2 = check_freshness(rep2, suspended, calendar, date(2026, 9, 26), 30, 2)
    assert rep2.sessions_behind_index == 5
    assert rep2.is_stale
    ok, notes = data_filter(rep2)
    assert not ok and notes

    _, rep3 = clean_bars(bars, "Z")
    rep3 = check_freshness(rep3, bars, calendar, date(2026, 10, 15), 5, 2)
    assert rep3.is_stale and rep3.data_age_days == 20


def test_liquidity_filter():
    cfg = LiquiditySettings(min_avg_turnover_try=50e6, min_avg_volume=1e5)
    assert liquidity_filter({"avg_turnover": 80e6, "avg_volume": 2e6}, cfg)[0]
    ok, reasons = liquidity_filter({"avg_turnover": 10e6, "avg_volume": 2e6}, cfg)
    assert not ok and "turnover" in reasons[0]
    assert not liquidity_filter({"avg_turnover": float("nan"), "avg_volume": 2e6}, cfg)[0]
    assert not liquidity_filter({"avg_turnover": 80e6, "avg_volume": 10}, cfg)[0]
    # Default: turnover only, so a high-priced stock with few shares traded passes.
    default = LiquiditySettings()
    assert liquidity_filter({"avg_turnover": 237e6, "avg_volume": 41_573}, default)[0]
