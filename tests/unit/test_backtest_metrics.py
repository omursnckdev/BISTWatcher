from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bist_quant.backtest.engine import Trade
from bist_quant.backtest.metrics import (
    benchmark_equity,
    equal_weight_equity,
    equity_metrics,
    trade_metrics,
    yearly_returns,
)
from bist_quant.backtest.walk_forward import make_folds


def _trade(pnl: float, ret: float, r: float) -> Trade:
    d = pd.Timestamp("2024-01-02")
    return Trade("X", d, d, 100, 100, 10, pnl, ret, r, 5, "stop", 80, "BULL", 1.0)


def test_equity_metrics_known_values():
    idx = pd.date_range("2020-01-01", "2022-01-01", freq="D")
    eq = pd.Series(np.linspace(100, 121, len(idx)), index=idx)
    m = equity_metrics(eq)
    assert m["total_return_pct"] == pytest.approx(21.0)
    assert m["cagr_pct"] == pytest.approx(10.0, abs=0.05)
    assert m["max_drawdown_pct"] == 0

    dd = pd.Series([100, 120, 90, 95, 130], index=pd.bdate_range("2024-01-01", periods=5))
    m2 = equity_metrics(dd)
    assert m2["max_drawdown_pct"] == pytest.approx(-25.0)
    assert m2["max_dd_days"] == 2  # below the peak from Jan 3 until the new high on Jan 5


def test_trade_metrics():
    m = trade_metrics([_trade(200, 2, 2), _trade(-100, -1, -1), _trade(-100, -1, -1)])
    assert m["trades"] == 3
    assert m["win_rate_pct"] == pytest.approx(100 / 3)
    assert m["profit_factor"] == pytest.approx(1.0)
    assert m["expectancy_r"] == pytest.approx(0.0)
    assert trade_metrics([])["trades"] == 0


def test_benchmarks_and_yearly():
    cal = pd.bdate_range("2023-12-28", "2024-12-31")
    close = pd.Series(np.linspace(100, 200, len(cal)), index=cal)
    eq = benchmark_equity(close, cal, 1000)
    assert eq.iloc[0] == 1000 and eq.iloc[-1] == pytest.approx(2000)
    yr = yearly_returns(eq)
    assert list(yr.index) == [2023, 2024]
    assert (1 + yr / 100).prod() == pytest.approx(2.0)

    a = pd.Series([100, 110, 121], index=cal[:3])
    b = pd.Series([100, 90, 81], index=cal[:3])
    ew = equal_weight_equity({"a": a, "b": b}, cal[:3], 1000)
    assert ew.iloc[1] == pytest.approx(1000)  # +10% and -10% average to 0


def test_make_folds():
    folds = make_folds(pd.Timestamp("2016-01-01"), pd.Timestamp("2021-06-30"), 3, 1)
    assert len(folds) == 3
    assert folds[0][2] == pd.Timestamp("2019-01-01")
    assert folds[-1][3] == pd.Timestamp("2021-06-30")
    assert all(f[1] < f[2] for f in folds)


def test_rank_ic_handles_constant_days():
    import warnings

    from bist_quant.backtest.research import _daily_rank_ic

    days = pd.to_datetime(["2024-01-02"] * 6 + ["2024-01-03"] * 6)
    panel = pd.DataFrame(
        {"score": [1, 2, 3, 4, 5, 6] * 2, "fwd_5d": [1, 2, 3, 4, 5, 6] + [0.0] * 6},
        index=pd.Index(days, name="date"),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ic = _daily_rank_ic(panel, "fwd_5d")
    assert list(ic.index) == [pd.Timestamp("2024-01-02")]  # constant day dropped silently
    assert ic.iloc[0] == pytest.approx(1.0)
