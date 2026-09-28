"""Factor research: do scores / signals predict forward returns?

Forward returns are measured from the **next session's open** (the earliest
realistic entry after a signal at the close) to the close ``h`` sessions later:
``fwd_h = close[t+h] / open[t+1] - 1``. Excess return subtracts the universe's
average forward return on the same date, isolating stock selection from market
direction. No costs are applied here - this measures raw predictive content.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from bist_quant.backtest.runner import BacktestContext
from bist_quant.config import Settings


@dataclass
class ResearchResult:
    by_bucket: pd.DataFrame
    by_signal: pd.DataFrame
    by_regime: pd.DataFrame
    information_coefficient: pd.DataFrame  # mean / t-stat of daily rank IC per horizon
    observations: int


def build_panel(ctx: BacktestContext, settings: Settings) -> pd.DataFrame:
    horizons = settings.research.horizons
    scored = ctx.scored(settings)
    signals = ctx.classified(settings).signals
    features = ctx.features_cache.features(settings)
    regimes = scored.regime.frame["regime"]
    parts = []
    for sym, sc in scored.scores.items():
        f = features[sym]
        nxt_open = f["open"].shift(-1)
        frame = pd.DataFrame(
            {"symbol": sym, "score": sc["score"], "signal": signals[sym]}, index=sc.index
        )
        for h in horizons:
            fwd = f["close"].shift(-h) / nxt_open - 1
            frame[f"fwd_{h}d"] = 100 * fwd.reindex(sc.index)
        parts.append(frame)
    panel = pd.concat(parts)
    panel.index.name = "date"
    panel["regime"] = regimes.reindex(panel.index).to_numpy()
    for h in horizons:
        col = f"fwd_{h}d"
        panel[f"xs_{h}d"] = panel[col] - panel.groupby(level="date")[col].transform("mean")
    return panel


def _summary(panel: pd.DataFrame, by: str | pd.Series, horizons: list[int]) -> pd.DataFrame:
    g = panel.groupby(by, observed=True)
    out = {"count": g.size()}
    for h in horizons:
        out[f"fwd_{h}d_mean"] = g[f"fwd_{h}d"].mean()
        out[f"xs_{h}d_mean"] = g[f"xs_{h}d"].mean()
        out[f"hit_{h}d_pct"] = g[f"fwd_{h}d"].apply(lambda s: 100 * (s.dropna() > 0).mean())
    return pd.DataFrame(out)


def _daily_rank_ic(panel: pd.DataFrame, col: str) -> pd.Series:
    df = panel[["score", col]].dropna()
    ranks = df.groupby(level="date").rank()
    joined = ranks.groupby(level="date")
    return joined.apply(lambda g: g["score"].corr(g[col]) if len(g) >= 5 else np.nan).dropna()


def run_research(ctx: BacktestContext, settings: Settings) -> ResearchResult:
    horizons = settings.research.horizons
    panel = build_panel(ctx, settings)
    buckets = pd.cut(
        panel["score"], settings.research.score_buckets, include_lowest=True, right=False
    )
    ic_rows = {}
    for h in horizons:
        ic = _daily_rank_ic(panel, f"fwd_{h}d")
        # Overlapping h-day windows are autocorrelated: scale t-stat by sqrt(h).
        t = ic.mean() / ic.std(ddof=1) * np.sqrt(len(ic) / h) if len(ic) > 1 else np.nan
        ic_rows[f"{h}d"] = {
            "mean_ic": ic.mean(),
            "t_stat": t,
            "positive_days_pct": 100 * (ic > 0).mean(),
            "days": len(ic),
        }
    return ResearchResult(
        by_bucket=_summary(panel, buckets, horizons),
        by_signal=_summary(panel, "signal", horizons),
        by_regime=_summary(panel, "regime", horizons),
        information_coefficient=pd.DataFrame(ic_rows).T,
        observations=len(panel),
    )
