"""Performance metrics for equity curves and trade lists, plus benchmarks."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

from bist_quant.backtest.engine import BacktestResult, Trade

TRADING_DAYS = 252


def _safe(x: float) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def equity_metrics(equity: pd.Series, risk_free_annual_pct: float = 0.0) -> dict[str, float | None]:
    """CAGR, volatility, Sharpe, Sortino, max drawdown (+duration), Calmar."""
    equity = equity.dropna()
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return {
            k: None
            for k in (
                "total_return_pct",
                "cagr_pct",
                "volatility_pct",
                "sharpe",
                "sortino",
                "max_drawdown_pct",
                "max_dd_days",
                "calmar",
            )
        }
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    rf_daily = (1 + risk_free_annual_pct / 100) ** (1 / TRADING_DAYS) - 1
    excess = rets - rf_daily
    std = rets.std(ddof=1)
    downside = np.sqrt((np.minimum(excess, 0) ** 2).mean())
    peak = equity.cummax()
    dd = equity / peak - 1
    max_dd = dd.min()
    # Longest stretch (calendar days) below a previous peak.
    underwater = dd < 0
    longest, run_start = 0, None
    for d, u in underwater.items():
        if u and run_start is None:
            run_start = d
        elif not u and run_start is not None:
            longest = max(longest, (d - run_start).days)
            run_start = None
    if run_start is not None:
        longest = max(longest, (equity.index[-1] - run_start).days)
    return {
        "total_return_pct": _safe(100 * total),
        "cagr_pct": _safe(100 * cagr),
        "volatility_pct": _safe(100 * std * math.sqrt(TRADING_DAYS)),
        "sharpe": _safe(excess.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else None,
        "sortino": _safe(excess.mean() / downside * math.sqrt(TRADING_DAYS))
        if downside > 0
        else None,
        "max_drawdown_pct": _safe(100 * max_dd),
        "max_dd_days": longest,
        "calmar": _safe(cagr / abs(max_dd)) if max_dd < 0 else None,
    }


def trade_metrics(trades: Sequence[Trade]) -> dict[str, float | int | None]:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    pnl = np.array([t.pnl for t in trades])
    rets = np.array([t.return_pct for t in trades])
    rs = np.array([t.r_multiple for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    return {
        "trades": n,
        "win_rate_pct": 100 * len(wins) / n,
        "loss_rate_pct": 100 * len(losses) / n,
        "avg_winner_pct": _safe(rets[pnl > 0].mean()) if len(wins) else None,
        "avg_loser_pct": _safe(rets[pnl <= 0].mean()) if len(losses) else None,
        "avg_winner_try": _safe(wins.mean()) if len(wins) else None,
        "avg_loser_try": _safe(losses.mean()) if len(losses) else None,
        "profit_factor": _safe(gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy_try": float(pnl.mean()),
        "expectancy_r": float(rs.mean()),
        "avg_holding_days": float(np.mean([t.holding_days for t in trades])),
        "best_trade_pct": float(rets.max()),
        "worst_trade_pct": float(rets.min()),
        "total_costs_try": float(sum(t.costs for t in trades)),
    }


def result_metrics(result: BacktestResult) -> dict[str, float | int | None]:
    bt = result.settings.backtest
    m: dict[str, float | int | None] = {}
    m.update(equity_metrics(result.equity, bt.risk_free_rate_annual_pct))
    m.update(trade_metrics(result.trades))
    daily = result.daily
    if len(daily):
        net_profit = float(result.equity.iloc[-1] - result.equity.iloc[0])
        dd_amount = float((result.equity.cummax() - result.equity).max())
        m["net_profit_try"] = net_profit
        m["final_equity_try"] = float(result.equity.iloc[-1])
        m["recovery_factor"] = _safe(net_profit / dd_amount) if dd_amount > 0 else None
        m["exposure_pct"] = float(100 * (daily["invested"] / daily["equity"]).mean())
        m["time_in_market_pct"] = float(100 * (daily["positions"] > 0).mean())
    return m


def benchmark_equity(close: pd.Series, calendar: pd.DatetimeIndex, initial: float) -> pd.Series:
    """Buy & hold ``close`` over ``calendar`` (no costs), starting at ``initial``."""
    series = close.reindex(close.index.union(calendar)).ffill().reindex(calendar).dropna()
    return initial * series / series.iloc[0]


def equal_weight_equity(
    closes: dict[str, pd.Series], calendar: pd.DatetimeIndex, initial: float
) -> pd.Series:
    """Daily-rebalanced equal-weight portfolio of every symbol trading that day."""
    panel = pd.DataFrame(closes).reindex(calendar)
    rets = panel.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0.0)
    rets.iloc[0] = 0.0
    return initial * (1 + rets).cumprod()


def yearly_returns(equity: pd.Series) -> pd.Series:
    """Calendar-year returns in percent (first year measured from the first value)."""
    year_end = equity.groupby(equity.index.year).last()
    start = pd.Series([equity.iloc[0]], index=[year_end.index[0] - 1])
    chained = pd.concat([start, year_end])
    return (100 * chained.pct_change().dropna()).rename("return_pct")


def exit_reason_breakdown(trades: Sequence[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(columns=["count", "share_pct", "avg_return_pct", "avg_r"])
    df = pd.DataFrame([t.__dict__ for t in trades])
    g = df.groupby("exit_reason")
    out = pd.DataFrame(
        {
            "count": g.size(),
            "avg_return_pct": g["return_pct"].mean(),
            "avg_r": g["r_multiple"].mean(),
        }
    )
    out.insert(1, "share_pct", 100 * out["count"] / len(df))
    return out.sort_values("count", ascending=False)
