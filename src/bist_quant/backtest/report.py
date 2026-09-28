"""Plain-text rendering of backtest, sweep, walk-forward and research results."""

from __future__ import annotations

import pandas as pd

from bist_quant.backtest.engine import BacktestResult
from bist_quant.backtest.metrics import (
    equity_metrics,
    exit_reason_breakdown,
    result_metrics,
    yearly_returns,
)
from bist_quant.backtest.research import ResearchResult
from bist_quant.backtest.walk_forward import WalkForwardResult

BIAS_NOTE = (
    "Caveats: universe = today's index members (survivorship bias inflates results); "
    "Yahoo data; costs are the configured placeholders; past performance does not "
    "predict future returns."
)


def _f(v, fmt: str = "{:,.2f}") -> str:
    return "-" if v is None or (isinstance(v, float) and pd.isna(v)) else fmt.format(v)


def format_backtest(
    result: BacktestResult, benchmarks: dict[str, pd.Series], universe: str, symbols: int
) -> str:
    s = result.settings
    bt = s.backtest
    m = result_metrics(result)
    eq = result.equity
    out = [
        "BIST QUANT BACKTEST",
        f"Period: {eq.index[0].date()} -> {eq.index[-1].date()}   Universe: {universe} "
        f"({symbols} symbols)   Entry: {bt.entry_mode}",
        f"Costs per side: commission {bt.commission_pct:.3%} + fees {bt.exchange_fee_pct:.3%}"
        f" + slippage {bt.slippage_pct:.3%}   Risk/trade {s.risk.risk_per_trade_pct}%  "
        f"Max positions {s.risk.max_open_positions}",
        "",
    ]
    rows = [("Strategy", m)]
    for name, series in benchmarks.items():
        rows.append((name, equity_metrics(series, bt.risk_free_rate_annual_pct)))
    head = f"{'':<24}{'Total %':>10}{'CAGR %':>9}{'MaxDD %':>9}{'Sharpe':>8}{'Sortino':>9}"
    out += [head, "-" * len(head)]
    for name, mm in rows:
        out.append(
            f"{name:<24}{_f(mm.get('total_return_pct'), '{:,.1f}'):>10}"
            f"{_f(mm.get('cagr_pct'), '{:.1f}'):>9}{_f(mm.get('max_drawdown_pct'), '{:.1f}'):>9}"
            f"{_f(mm.get('sharpe')):>8}{_f(mm.get('sortino')):>9}"
        )
    out += [
        "",
        "Trades",
        f"  Total trades        {m.get('trades', 0)}",
        f"  Win / loss rate     {_f(m.get('win_rate_pct'), '{:.1f}')}% / "
        f"{_f(m.get('loss_rate_pct'), '{:.1f}')}%",
        f"  Avg winner / loser  {_f(m.get('avg_winner_pct'))}% / {_f(m.get('avg_loser_pct'))}%",
        f"  Profit factor       {_f(m.get('profit_factor'))}",
        f"  Expectancy          {_f(m.get('expectancy_r'))} R  "
        f"({_f(m.get('expectancy_try'), '{:,.0f}')} TRY per trade)",
        f"  Best / worst trade  {_f(m.get('best_trade_pct'))}% / {_f(m.get('worst_trade_pct'))}%",
        f"  Avg holding         {_f(m.get('avg_holding_days'), '{:.1f}')} sessions",
        f"  Exposure            {_f(m.get('exposure_pct'), '{:.1f}')}% of equity invested on avg,"
        f" in market {_f(m.get('time_in_market_pct'), '{:.0f}')}% of days",
        f"  Max drawdown        {_f(m.get('max_drawdown_pct'), '{:.1f}')}%, longest underwater "
        f"{_f(m.get('max_dd_days'), '{:,}')} days",
        f"  Recovery factor     {_f(m.get('recovery_factor'))}",
        f"  Net profit          {_f(m.get('net_profit_try'), '{:,.0f}')} TRY "
        f"(costs paid {_f(m.get('total_costs_try'), '{:,.0f}')} TRY)",
        f"  Orders              {result.stats}",
    ]
    if result.trades:
        out += ["", "Exit reasons"]
        br = exit_reason_breakdown(result.trades)
        for reason, r in br.iterrows():
            out.append(
                f"  {reason:<16}{int(r['count']):>5}  {r['share_pct']:5.1f}%  "
                f"avg {r['avg_return_pct']:+6.2f}%  {r['avg_r']:+5.2f}R"
            )
        out += ["", "By regime at entry"]
        tf = result.trades_frame()
        for regime, g in tf.groupby("regime"):
            out.append(
                f"  {regime:<16}{len(g):>5}  win {100 * (g['pnl'] > 0).mean():5.1f}%  "
                f"avg {g['return_pct'].mean():+6.2f}%  {g['r_multiple'].mean():+5.2f}R"
            )
    yr = yearly_returns(eq)
    bench_name = next(iter(benchmarks))
    byr = yearly_returns(benchmarks[bench_name])
    out += ["", f"{'Year':<8}{'Strategy %':>12}{bench_name + ' %':>24}"]
    for year, v in yr.items():
        out.append(f"{year:<8}{v:>12.1f}{_f(byr.get(year), '{:.1f}'):>24}")
    out += ["", BIAS_NOTE]
    return "\n".join(out)


SWEEP_COLS = [
    "trades",
    "win_rate_pct",
    "profit_factor",
    "expectancy_r",
    "cagr_pct",
    "max_drawdown_pct",
    "sharpe",
    "exposure_pct",
]


def format_sweep(df: pd.DataFrame, params: list[str]) -> str:
    cols = params + [c for c in SWEEP_COLS if c in df.columns]
    shown = df[cols].copy()

    def fmt(v, digits: int) -> str:
        return "-" if v is None or pd.isna(v) else f"{v:.{digits}f}"

    for c in SWEEP_COLS:
        if c in shown:
            digits = 0 if c == "trades" else 2
            shown[c] = [fmt(v, digits) for v in shown[c]]
    return "PARAMETER SWEEP (same period, full costs)\n" + shown.to_string(index=False)


def format_walk_forward(wf: WalkForwardResult, benchmark: dict[str, pd.Series]) -> str:
    out = ["WALK-FORWARD VALIDATION", ""]
    for i, f in enumerate(wf.folds, 1):
        ins, oos = f.in_sample, f.out_of_sample
        out.append(
            f"Fold {i}: train {f.train_start.date()}..{f.train_end.date()}  "
            f"test {f.test_start.date()}..{f.test_end.date()}  params {f.params or 'defaults'}"
        )
        for label, mm in (("in-sample ", ins), ("out-sample", oos)):
            out.append(
                f"    {label} trades {mm.get('trades', 0):>4}  "
                f"CAGR {_f(mm.get('cagr_pct'), '{:6.1f}')}%  Sharpe {_f(mm.get('sharpe'))}  "
                f"PF {_f(mm.get('profit_factor'))}"
            )
    m = wf.oos_metrics
    out += ["", "Combined out-of-sample:"]
    out.append(
        f"  Strategy               CAGR {_f(m.get('cagr_pct'), '{:.1f}')}%  MaxDD "
        f"{_f(m.get('max_drawdown_pct'), '{:.1f}')}%  Sharpe {_f(m.get('sharpe'))}  "
        f"trades {m.get('trades', 0)}  PF {_f(m.get('profit_factor'))}  "
        f"expectancy {_f(m.get('expectancy_r'))}R"
    )
    for name, series in benchmark.items():
        bm = equity_metrics(series)
        out.append(
            f"  {name:<22} CAGR {_f(bm.get('cagr_pct'), '{:.1f}')}%  MaxDD "
            f"{_f(bm.get('max_drawdown_pct'), '{:.1f}')}%  Sharpe {_f(bm.get('sharpe'))}"
        )
    out += ["", BIAS_NOTE]
    return "\n".join(out)


def format_research(r: ResearchResult, horizons: list[int]) -> str:
    show = [h for h in horizons if h in (5, 10, 20)] or horizons[:3]

    def table(df: pd.DataFrame, title: str) -> list[str]:
        head = f"{title:<22}{'n':>8}" + "".join(
            f"{f'fwd{h}d%':>9}{f'xs{h}d%':>8}{f'hit{h}d':>8}" for h in show
        )
        lines = [head, "-" * len(head)]
        for key, row in df.iterrows():
            lines.append(
                f"{str(key):<22}{int(row['count']):>8}"
                + "".join(
                    f"{_f(row[f'fwd_{h}d_mean'], '{:+.2f}'):>9}"
                    f"{_f(row[f'xs_{h}d_mean'], '{:+.2f}'):>8}"
                    f"{_f(row[f'hit_{h}d_pct'], '{:.0f}'):>7}%"
                    for h in show
                )
            )
        return lines

    out = [
        "FACTOR RESEARCH (no costs; entry at next open; xs = excess vs universe average)",
        f"Observations: {r.observations:,} symbol-sessions",
        "",
    ]
    out += table(r.by_bucket, "Score bucket") + [""]
    out += table(r.by_signal, "Signal") + [""]
    out += table(r.by_regime, "Regime") + [""]
    out.append("Rank information coefficient (score vs forward return, per day):")
    for h, row in r.information_coefficient.iterrows():
        out.append(
            f"  {h:>4}: mean IC {row['mean_ic']:+.3f}  t-stat {row['t_stat']:+.2f}  "
            f"IC>0 on {row['positive_days_pct']:.0f}% of {int(row['days'])} days"
        )
    out += ["", BIAS_NOTE]
    return "\n".join(out)
