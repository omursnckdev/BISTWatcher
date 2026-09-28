"""Factor research: do scores / signals predict forward returns?

Forward returns are measured from the **next session's open** (the earliest
realistic entry after a signal at the close) to the close ``h`` sessions later:
``fwd_h = close[t+h] / open[t+1] - 1``. Excess return subtracts the universe's
average forward return on the same date, isolating stock selection from market
direction. No costs are applied here - this measures raw predictive content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

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


def _rank_corr(g: pd.DataFrame, col: str) -> float:
    """Pearson correlation of ranks; NaN when undefined (few rows or a constant column)."""
    if len(g) < 5:
        return np.nan
    a = g["score"].to_numpy(float) - g["score"].mean()
    b = g[col].to_numpy(float) - g[col].mean()
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / denom) if denom > 0 else np.nan


def _daily_rank_ic(panel: pd.DataFrame, col: str) -> pd.Series:
    df = panel[["score", col]].dropna()
    ranks = df.groupby(level="date").rank()
    return ranks.groupby(level="date").apply(_rank_corr, col).dropna()


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


# --------------------------------------------------------------------------- event study


def _universe_forward(features: dict[str, pd.DataFrame], horizons: list[int]) -> dict:
    """Equal-weight universe return from the open of day d to the close h-1 days later."""
    opens = pd.DataFrame({s: f["open"] for s, f in features.items()})
    closes = pd.DataFrame({s: f["close"] for s, f in features.items()})
    return {h: (closes.shift(-(h - 1)) / opens - 1).mean(axis=1) for h in horizons}


def _event_rows(ctx: BacktestContext, settings: Settings) -> pd.DataFrame:
    """One row per (symbol, event) with forward returns in excess of the equal-weight
    universe (same entry day and horizon), after two entry points:

    * ``news``: next open after the session whose news cutoff includes the event
    * ``reaction``: next open after the price reaction became observable
    """
    book = ctx.scorer.news_book(settings)
    if book is None:
        return pd.DataFrame()
    features = ctx.features_cache.features(settings)
    horizons = settings.research.horizons
    universe = _universe_forward(features, horizons)
    rows = []
    for sym, events in book.events.items():
        f = features.get(sym)
        if f is None or len(f) < 3:
            continue
        dates = f.index
        opens, closes = f["open"].to_numpy(float), f["close"].to_numpy(float)
        bench = {h: universe[h].reindex(dates).to_numpy(float) for h in horizons}
        for e in events:
            cutoff_day = e.article.published_at.date()
            if e.article.published_at.timetz().replace(tzinfo=None) > book.cutoff_time:
                cutoff_day = cutoff_day + timedelta(days=1)
            entries = {"news": int(dates.searchsorted(pd.Timestamp(cutoff_day))) + 1}
            if e.reaction_known_at is not None:
                k = int(dates.searchsorted(pd.Timestamp(e.reaction_known_at.date())))
                entries["reaction"] = k + 1
            c = e.classification
            for kind, i in entries.items():
                if i >= len(dates):
                    continue
                rec = {
                    "symbol": sym,
                    "kind": kind,
                    "event_type": c.event_type.value,
                    "sentiment": c.sentiment,
                    "reaction": e.reaction or "n/a",
                    "date": dates[i],
                }
                for h in horizons:
                    j = i + h - 1
                    if j < len(dates) and np.isfinite(bench[h][i]):
                        stock = closes[j] / opens[i] - 1
                        rec[f"xs_{h}d"] = 100 * (stock - bench[h][i])
                rows.append(rec)
    return pd.DataFrame(rows)


def _event_summary(df: pd.DataFrame, by: str | list[str], horizons: list[int]) -> pd.DataFrame:
    g = df.groupby(by, observed=True)
    out = {"count": g.size()}
    for h in horizons:
        col = f"xs_{h}d"
        mean, std, n = g[col].mean(), g[col].std(ddof=1), g[col].count()
        out[f"xs_{h}d_mean"] = mean
        out[f"t_{h}d"] = mean / std * np.sqrt(n)
        out[f"hit_{h}d_pct"] = g[col].apply(lambda s: 100 * (s.dropna() > 0).mean())
    return pd.DataFrame(out)


@dataclass
class EventStudyResult:
    by_type: pd.DataFrame  # event type x sentiment sign, entry after the news
    by_reaction: pd.DataFrame  # news-vs-price reaction label, entry after the reaction
    events: int


def run_event_study(ctx: BacktestContext, settings: Settings) -> EventStudyResult | None:
    df = _event_rows(ctx, settings)
    if df.empty:
        return None
    horizons = settings.research.horizons
    news = df[df["kind"] == "news"].copy()
    news["tone"] = np.select(
        [news["sentiment"] > 0.1, news["sentiment"] < -0.1], ["positive", "negative"], "neutral"
    )
    reaction = df[(df["kind"] == "reaction") & (df["reaction"] != "n/a")]
    by_type = _event_summary(news, ["event_type", "tone"], horizons)
    by_type = by_type[by_type["count"] >= 20].sort_values("count", ascending=False)
    by_reaction = (
        _event_summary(reaction, "reaction", horizons) if len(reaction) else pd.DataFrame()
    )
    return EventStudyResult(by_type, by_reaction, int(len(news)))
