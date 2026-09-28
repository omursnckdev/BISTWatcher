"""Walk-forward validation: choose parameters in-sample, measure them out-of-sample.

    |--- train (N years) ---|-- test (M years) --|
                  |--- train ---|-- test --|           (step = M years)

Parameter sets with too few in-sample trades are ignored; if none qualify, the
first grid combination (the configured defaults should be listed first) is used.
The out-of-sample test segments are chained into one equity curve.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bist_quant.backtest.engine import Trade
from bist_quant.backtest.metrics import equity_metrics, result_metrics, trade_metrics
from bist_quant.backtest.runner import BacktestContext, expand_grid
from bist_quant.config import Settings, apply_overrides


@dataclass
class Fold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    params: dict
    in_sample: dict
    out_of_sample: dict
    candidates_tested: int


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    oos_equity: pd.Series
    oos_trades: list[Trade]
    oos_metrics: dict


def make_folds(start: pd.Timestamp, end: pd.Timestamp, train_years: int, test_years: int):
    folds = []
    t0 = start
    while True:
        test_start = t0 + pd.DateOffset(years=train_years)
        if test_start > end:
            break
        test_end = min(test_start + pd.DateOffset(years=test_years) - pd.Timedelta(days=1), end)
        folds.append((t0, test_start - pd.Timedelta(days=1), test_start, test_end))
        t0 = t0 + pd.DateOffset(years=test_years)
    return folds


def walk_forward(ctx: BacktestContext, base: Settings) -> WalkForwardResult:
    wf = base.walk_forward
    combos = expand_grid(wf.grid)
    variants = [(c, apply_overrides(base, c) if c else base) for c in combos]
    cal = ctx.calendar(base)
    folds: list[Fold] = []
    segments: list[pd.Series] = []
    trades: list[Trade] = []
    for tr_s, tr_e, te_s, te_e in make_folds(cal[0], cal[-1], wf.train_years, wf.test_years):
        scored = []
        for combo, settings in variants:
            m = result_metrics(ctx.run(settings, tr_s, tr_e))
            scored.append((combo, settings, m))
        eligible = [x for x in scored if (x[2].get("trades") or 0) >= wf.min_trades]
        pool = eligible or scored[:1]
        best = max(pool, key=lambda x: _objective(x[2], wf.objective))
        test = ctx.run(best[1], te_s, te_e)
        folds.append(
            Fold(tr_s, tr_e, te_s, te_e, best[0], best[2], result_metrics(test), len(scored))
        )
        if len(test.equity):
            segments.append(test.equity.pct_change().fillna(0.0))
        trades.extend(test.trades)
    initial = base.backtest.initial_equity
    if segments:
        rets = pd.concat(segments)
        rets = rets[~rets.index.duplicated()]
        oos_equity = initial * (1 + rets).cumprod()
    else:
        oos_equity = pd.Series(dtype=float)
    metrics = equity_metrics(oos_equity, base.backtest.risk_free_rate_annual_pct)
    metrics.update(trade_metrics(trades))
    return WalkForwardResult(folds, oos_equity, trades, metrics)


def _objective(m: dict, name: str) -> float:
    key = {
        "sharpe": "sharpe",
        "sortino": "sortino",
        "cagr": "cagr_pct",
        "profit_factor": "profit_factor",
        "expectancy_r": "expectancy_r",
    }[name]
    value = m.get(key)
    return float("-inf") if value is None else float(value)
