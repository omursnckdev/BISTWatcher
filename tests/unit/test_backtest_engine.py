"""Hand-computed scenarios for the portfolio simulator."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from bist_quant.backtest.engine import run_backtest
from bist_quant.backtest.signals import Candidate
from bist_quant.config import Settings, apply_overrides
from bist_quant.models.signals import SignalType

DAYS = pd.bdate_range("2024-01-01", periods=30)


def bars(
    rows: list[tuple[float, float, float, float]],
    atr: float = 2.0,
    ema: float = 0.0,
    macd: float = 1.0,
    sig: float = 0.0,
) -> pd.DataFrame:
    """rows: (open, high, low, close); indicator columns constant (trend exit off by default)."""
    idx = DAYS[: len(rows)]
    f = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    f["atr"], f["ema_fast"], f["macd"], f["macd_signal"] = atr, ema, macd, sig
    return f


def settings(**over) -> Settings:
    base = {
        "backtest.commission_pct": 0.0,
        "backtest.exchange_fee_pct": 0.0,
        "backtest.slippage_pct": 0.0,
        "backtest.entry_mode": "next_open",
        "backtest.initial_equity": 100_000,
        "risk.risk_per_trade_pct": 1.0,
        "risk.max_position_pct": 100.0,
        "risk.max_portfolio_risk_pct": 100.0,
        "risk.max_sector_exposure_pct": 100.0,
        "backtest.exits.trend_exit": False,
        "backtest.exits.time_stop_days": 50,
        "backtest.exits.max_holding_days": 50,
    }
    base.update(over)
    return apply_overrides(Settings(), base)


def run(
    s: Settings,
    f: pd.DataFrame,
    signal_day: int = 0,
    close: float | None = None,
    atr: float = 2.0,
    symbol: str = "AAA",
):
    cand = Candidate(
        symbol,
        80.0,
        SignalType.BUY_CANDIDATE,
        close if close is not None else f["close"].iloc[signal_day],
        atr,
        2.5,
    )
    cands = {f.index[signal_day]: [cand]}
    regimes = pd.Series("BULL", index=f.index)
    return run_backtest(s, {symbol: f}, cands, regimes, f.index)


def test_stop_loss_exit_and_sizing():
    # Signal day 0; fill day 1 at open 100; stop = 100 - 2*2 = 96; risk 1% of 100k = 1000
    f = bars([(100, 101, 99, 100), (100, 101, 99, 100), (99, 99, 95, 96), (96, 97, 95, 96)])
    r = run(settings(), f)
    (t,) = r.trades
    assert t.shares == 250  # 1000 / 4
    assert t.entry_price == 100 and t.exit_price == 96
    assert t.exit_reason == "stop" and t.exit_date == f.index[2]
    assert t.pnl == pytest.approx(-1000) and t.r_multiple == pytest.approx(-1.0)
    assert r.equity.iloc[-1] == pytest.approx(99_000)


def test_gap_through_stop_exits_at_open():
    f = bars([(100, 101, 99, 100), (100, 101, 99, 100), (90, 92, 89, 91), (91, 92, 90, 91)])
    (t,) = run(settings(), f).trades
    assert t.exit_price == 90 and t.exit_reason == "stop"
    assert t.r_multiple == pytest.approx(-2.5)


def test_tp1_partial_breakeven_then_tp2():
    # R = 4 -> TP1 = 106, TP2 = 110. Half sold at TP1, stop -> 100, rest at TP2.
    f = bars(
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (101, 107, 100.5, 106),
            (106, 111, 105, 110),
            (110, 111, 109, 110),
        ]
    )
    r = run(settings(), f)
    (t,) = r.trades
    assert t.exit_reason == "tp2"
    assert t.pnl == pytest.approx(125 * 6 + 125 * 10)
    assert t.r_multiple == pytest.approx(2.0)


def test_breakeven_stop_after_tp1():
    f = bars(
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (101, 107, 100.5, 106),
            (105, 105, 99, 100),
            (100, 101, 99, 100),
        ]
    )
    (t,) = run(settings(), f).trades
    assert t.exit_reason == "breakeven_stop"
    assert t.pnl == pytest.approx(125 * 6)  # remainder exits flat at the entry price


def test_same_bar_stop_and_target_assumes_stop_first():
    f = bars([(100, 101, 99, 100), (100, 101, 99, 100), (100, 112, 95, 105), (105, 106, 104, 105)])
    (t,) = run(settings(), f).trades
    assert t.exit_reason == "stop" and t.exit_price == 96


def test_zone_limit_fill_and_miss():
    s = settings(**{"backtest.entry_mode": "zone_limit"})
    # Signal close 100, ATR 2, zone 0.25 -> limit 100.5. Open 102 gaps above; low 100 -> fill 100.5
    f = bars([(100, 101, 99, 100), (102, 103, 100, 101), (101, 102, 100, 101)])
    r = run(s, f)
    assert r.stats["filled"] == 1
    assert r.trades[0].entry_price == pytest.approx(100.5)
    # Never trades down to the limit -> order expires, no trade.
    g = bars([(100, 101, 99, 100), (102, 104, 101, 103), (103, 104, 102, 103)])
    r2 = run(s, g)
    assert r2.stats["missed_limit"] == 1 and not r2.trades


def test_time_stop_and_max_hold():
    flat = [(100, 101, 99, 100)] * 8
    s = settings(**{"backtest.exits.time_stop_days": 3})
    (t,) = run(s, bars(flat)).trades
    assert t.exit_reason == "time_stop"
    assert t.entry_date == DAYS[1] and t.exit_date == DAYS[4]  # decided at close of day 3
    up = [(100 + i * 0.5, 101 + i * 0.5, 99.5 + i * 0.5, 100.5 + i * 0.5) for i in range(8)]
    s2 = settings(**{"backtest.exits.max_holding_days": 4})
    (t2,) = run(s2, bars(up)).trades
    assert t2.exit_reason == "max_hold" and t2.holding_days == 4


def test_trend_exit_next_open():
    f = bars([(100, 101, 99, 100)] * 5, ema=101, macd=-1, sig=0)
    s = settings(**{"backtest.exits.trend_exit": True})
    (t,) = run(s, f).trades
    assert t.exit_reason == "trend_exit" and t.exit_date == DAYS[2]


def test_costs_and_slippage():
    s = settings(**{"backtest.commission_pct": 0.001, "backtest.slippage_pct": 0.001})
    f = bars([(100, 101, 99, 100), (100, 101, 99, 100), (99, 99, 95, 96), (96, 97, 95, 96)])
    (t,) = run(s, f).trades
    entry = 100 * 1.001
    exit_ = 96 * 0.999
    shares = math.floor(1000 / (entry - 96))
    expected = shares * (exit_ - entry) - shares * entry * 0.001 - shares * exit_ * 0.001
    assert t.shares == shares
    assert t.pnl == pytest.approx(expected)
    assert t.costs == pytest.approx(shares * entry * 0.001 + shares * exit_ * 0.001)


def test_position_caps():
    f = bars([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    capped = settings(**{"risk.max_position_pct": 10.0})  # 10k / 100 = 100 shares
    assert run(capped, f).trades[0].shares == 100
    lots = settings(**{"risk.lot_size": 100})
    assert run(lots, f).trades[0].shares == 200


def test_end_of_test_liquidation_and_equity_reconciles():
    f = bars([(100, 101, 99, 100), (100, 101, 99, 100), (101, 102, 100, 102)])
    r = run(settings(), f)
    (t,) = r.trades
    assert t.exit_reason == "end_of_test" and t.exit_price == 102
    assert r.equity.iloc[-1] == pytest.approx(100_000 + t.pnl)


def test_max_open_positions():
    f = bars([(100, 101, 99, 100)] * 4)
    feats = {s: f for s in ("A", "B", "C")}
    cands = {
        f.index[0]: [
            Candidate(s, 90 - i, SignalType.BUY_CANDIDATE, 100, 2, 2.5) for i, s in enumerate(feats)
        ]
    }
    s = settings(**{"risk.max_open_positions": 2})
    r = run_backtest(s, feats, cands, pd.Series("BULL", index=f.index), f.index)
    assert {t.symbol for t in r.trades} == {"A", "B"}


def test_min_position_skips_token_entries():
    # 1% risk / 4 TRY per share = 250 shares = 25% of equity; a 0.5% portfolio-risk cap
    # leaves room for only 125 shares (12.5%): taken at min 10%, skipped at min 15%.
    f = bars([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    s = settings(**{"risk.max_portfolio_risk_pct": 0.5})
    (t,) = run(apply_overrides(s, {"backtest.min_position_pct": 10.0}), f).trades
    assert t.shares == 125
    r = run(apply_overrides(s, {"backtest.min_position_pct": 15.0}), f)
    assert not r.trades and r.stats["no_capacity"] == 1
