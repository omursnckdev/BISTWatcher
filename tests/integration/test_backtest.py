from __future__ import annotations

import asyncio
from datetime import date

import pytest

from bist_quant.backtest.data import load_history
from bist_quant.backtest.research import run_research
from bist_quant.backtest.runner import BacktestContext, sweep
from bist_quant.backtest.walk_forward import walk_forward
from bist_quant.config import apply_overrides
from bist_quant.data.market_data import SyntheticProvider
from bist_quant.data.universe import load_universe
from bist_quant.main import main

START, END = date(2019, 1, 2), date(2023, 12, 29)


@pytest.fixture(scope="module")
def ctx(settings):
    s = apply_overrides(settings, {"liquidity.min_avg_turnover_try": 0})
    symbols = load_universe(s.universe, "BIST30")[:15]
    hist = asyncio.run(load_history(s, symbols, SyntheticProvider(), START, END))
    return BacktestContext(hist), s


def test_backtest_runs_and_reconciles(ctx):
    context, s = ctx
    r = context.run(s)
    assert len(r.trades) > 20
    assert r.equity.iloc[-1] == pytest.approx(
        s.backtest.initial_equity + sum(t.pnl for t in r.trades)
    )
    assert (r.daily["cash"] >= -1e-6).all()
    assert r.daily["positions"].max() <= s.risk.max_open_positions
    tf = r.trades_frame().sort_values(["symbol", "entry_date"])
    for _, g in tf.groupby("symbol"):
        assert (g["entry_date"].iloc[1:].to_numpy() >= g["exit_date"].iloc[:-1].to_numpy()).all()


def test_backtest_is_deterministic(ctx):
    context, s = ctx
    a = context.run(s).trades_frame()
    b = context.run(s).trades_frame()
    assert a.equals(b)


def test_backtest_has_no_lookahead(ctx):
    """Trades that finished before a cut-off are identical whether or not later data exists."""
    context, s = ctx
    cut = "2021-06-30"
    full = context.run(s).trades_frame()
    part = context.run(s, end=cut).trades_frame()
    done_full = full[full["exit_date"] < cut].reset_index(drop=True)
    done_part = part[part["exit_reason"] != "end_of_test"].reset_index(drop=True)
    assert len(done_part) > 5
    assert done_full.equals(done_part)


def test_sweep_walkforward_research(ctx):
    context, s = ctx
    table = sweep(context, s, {"risk.use_resistance_cap": [True, False]})
    assert len(table) == 2 and "sharpe" in table
    s2 = apply_overrides(
        s,
        {
            "walk_forward.train_years": 2,
            "walk_forward.min_trades": 5,
            "walk_forward.grid": {"strategy.buy_threshold_offset": [0, 5]},
        },
    )
    wf = walk_forward(context, s2)
    assert len(wf.folds) >= 2
    assert len(wf.oos_equity) > 200
    res = run_research(context, s)
    assert res.observations > 1000
    assert {"5d", "20d"} <= set(res.information_coefficient.index)


def test_cli_backtest_synthetic(capsys):
    args = [
        "--provider",
        "synthetic",
        "--universe",
        "BIST30",
        "--start",
        "2020-01-02",
        "--end",
        "2022-12-30",
        "--set",
        "liquidity.min_avg_turnover_try=0",
    ]
    assert main(["backtest", *args]) == 0
    out = capsys.readouterr().out
    assert "BIST QUANT BACKTEST" in out and "XU100 buy&hold" in out and "Profit factor" in out
    assert main(["research", *args]) == 0
    assert "information coefficient" in capsys.readouterr().out
