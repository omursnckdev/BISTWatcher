from datetime import date

import numpy as np
import pandas as pd
import pytest

from bist_quant.config import apply_overrides
from bist_quant.strategy.exit_check import (
    ExitAction,
    Holding,
    check_holding,
    trend_exit_triggered,
)


def frame(closes, atr=2.0, start="2026-01-05", ema_gap=-5.0, macd=1.0, scale=1.0):
    """Feature frame with tight bars around ``closes``; healthy trend unless overridden."""
    idx = pd.bdate_range(start, periods=len(closes), name="date")
    c = np.asarray(closes, float)
    return pd.DataFrame(
        {
            "open": c,
            "high": c + 0.5,
            "low": c - 0.5,
            "close": c,
            "raw_close": c * scale,
            "atr": atr,
            "ema_fast": c + ema_gap,  # close above EMA20 by default
            "macd": macd,
            "macd_signal": 0.0,
        },
        index=idx,
    )


@pytest.fixture()
def cfg(settings):
    # no time stop / max hold interference unless a test asks for it
    return apply_overrides(
        settings,
        {"backtest.exits.time_stop_days": 1000, "backtest.exits.max_holding_days": 1000},
    )


def test_hold_when_nothing_triggers(cfg):
    f = frame([100, 101, 102, 101.5, 102.5])
    c = check_holding(Holding("AAA", date(2026, 1, 5), 100, 10), f, cfg)
    assert c.action is ExitAction.HOLD
    assert c.reasons == []
    assert c.initial_stop == pytest.approx(100 - 2 * 2.0)
    assert c.tp1 == pytest.approx(100 + 1.5 * 4)
    assert c.tp2 == pytest.approx(100 + 2.5 * 4)
    assert c.pnl_try == pytest.approx(25.0)
    assert c.sessions_held == 4


def test_stop_hit_sells_with_date(cfg):
    f = frame([100, 99, 95, 97, 99])  # low of 95.5 -> 94.5 on day 3 < stop 96
    c = check_holding(Holding("AAA", date(2026, 1, 5), 100, 10), f, cfg)
    assert c.action is ExitAction.SELL
    assert c.reasons == ["stop"]
    assert c.events["stop"] == date(2026, 1, 7)


def test_tp1_reached_asks_for_partial_and_moves_stop_to_breakeven(cfg):
    f = frame([100, 103, 106.5, 105])
    c = check_holding(Holding("AAA", date(2026, 1, 5), 100, 10), f, cfg)
    assert c.action is ExitAction.TAKE_PARTIAL
    assert c.tp1_reached
    assert c.stop == pytest.approx(100.0)


def test_tp1_already_taken_holds(cfg):
    f = frame([100, 103, 106.5, 105])
    c = check_holding(Holding("AAA", date(2026, 1, 5), 100, 10, tp1_taken=True), f, cfg)
    assert c.action is ExitAction.HOLD


def test_tp2_sells(cfg):
    f = frame([100, 104, 107, 110.5])
    c = check_holding(Holding("AAA", date(2026, 1, 5), 100, 10), f, cfg)
    assert c.action is ExitAction.SELL
    assert c.reasons == ["tp2"]


def test_trend_exit_on_last_close(cfg):
    f = frame([100, 101, 100.5])
    f.loc[f.index[-1], ["ema_fast", "macd"]] = [102.0, -1.0]
    c = check_holding(Holding("AAA", date(2026, 1, 5), 100, 10), f, cfg)
    assert c.action is ExitAction.SELL
    assert c.reasons == ["trend_exit"]
    assert c.events["trend_exit"] == f.index[-1].date()


def test_max_hold(settings):
    s = apply_overrides(settings, {"backtest.exits.max_holding_days": 3})
    f = frame([100, 100.5, 101, 101.5, 101.2, 101.4])
    c = check_holding(Holding("AAA", date(2026, 1, 5), 100, 10), f, s)
    assert c.action is ExitAction.SELL
    assert c.reasons == ["max_hold"]


def test_user_stop_and_traded_price_scale(cfg):
    # adjusted prices are half the traded prices (e.g. after a 2:1 bonus issue)
    f = frame([50, 50.5, 49.8, 50.2], atr=1.0, scale=2.0)
    c = check_holding(Holding("AAA", date(2026, 1, 5), 100, 10, stop=95), f, cfg)
    assert c.initial_stop == 95
    assert c.last_close == pytest.approx(100.4)
    assert c.action is ExitAction.HOLD
    auto = check_holding(Holding("AAA", date(2026, 1, 5), 100, 10), f, cfg)
    assert auto.initial_stop == pytest.approx(100 - 2 * 2.0)  # ATR converted to traded scale
    assert auto.action is ExitAction.HOLD  # lowest traded low (49.8 - 0.5) * 2 = 98.6 > 96


def test_entry_before_data(cfg):
    f = frame([100, 101])
    c = check_holding(Holding("AAA", date(2020, 1, 1), 100, 10), f, cfg)
    assert c.reasons == ["before_entry"]


def test_trend_exit_rule_handles_missing_values():
    assert not trend_exit_triggered(
        {"close": 1.0, "ema_fast": float("nan"), "macd": 0, "macd_signal": 1}
    )
    assert not trend_exit_triggered({})
    assert trend_exit_triggered({"close": 1.0, "ema_fast": 2.0, "macd": -1, "macd_signal": 0})
