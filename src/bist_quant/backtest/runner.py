"""Backtest orchestration with caching across parameter variants."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

import pandas as pd

from bist_quant.backtest.data import FeatureCache, HistoricalData, _key
from bist_quant.backtest.engine import BacktestResult, run_backtest
from bist_quant.backtest.metrics import benchmark_equity, equal_weight_equity, result_metrics
from bist_quant.backtest.signals import Candidate, ScoredPanel, Scorer, classify_panel
from bist_quant.config import Settings, apply_overrides
from bist_quant.logging import get_logger, log_event

log = get_logger(__name__)


@dataclass
class Classified:
    signals: dict[str, pd.Series]
    candidates: dict[pd.Timestamp, list[Candidate]]


class BacktestContext:
    """Holds loaded history; memoises features, scores and signals per configuration."""

    def __init__(self, history: HistoricalData, news_events: dict | None = None) -> None:
        self.history = history
        self.news_events = news_events
        self.features_cache = FeatureCache(history)
        self.scorer = Scorer(self.features_cache, news_events)
        self._classified: dict[str, Classified] = {}

    def scored(self, settings: Settings) -> ScoredPanel:
        return self.scorer.scored(settings)

    def classified(self, settings: Settings) -> Classified:
        key = _key(
            settings.indicators,
            settings.scoring,
            settings.market_regime,
            settings.strategy,
            settings.risk,
            settings.liquidity,
            settings.news,
        )
        if key not in self._classified:
            signals, cands = classify_panel(
                self.scored(settings), self.features_cache.features(settings), settings
            )
            self._classified[key] = Classified(signals, cands)
        return self._classified[key]

    def calendar(self, settings: Settings, start=None, end=None) -> pd.DatetimeIndex:
        regime_dates = self.scored(settings).regime.frame.index
        cal = self.history.calendar
        cal = cal[cal.isin(regime_dates)]
        if start is not None:
            cal = cal[cal >= pd.Timestamp(start)]
        if end is not None:
            cal = cal[cal <= pd.Timestamp(end)]
        return cal

    def run(self, settings: Settings, start=None, end=None) -> BacktestResult:
        cls = self.classified(settings)
        regimes = self.scored(settings).regime.frame["regime"]
        calendar = self.calendar(settings, start, end)
        result = run_backtest(
            settings, self.features_cache.features(settings), cls.candidates, regimes, calendar
        )
        log_event(
            log,
            "backtest_finished",
            start=str(calendar[0].date()) if len(calendar) else None,
            end=str(calendar[-1].date()) if len(calendar) else None,
            trades=len(result.trades),
        )
        return result

    def benchmarks(self, calendar: pd.DatetimeIndex, initial: float) -> dict[str, pd.Series]:
        out = {
            f"{name} buy&hold": benchmark_equity(close, calendar, initial)
            for name, close in self.history.benchmarks.items()
        }
        closes = {s: b["close"] for s, b in self.history.bars.items()}
        out["Equal-weight universe"] = equal_weight_equity(closes, calendar, initial)
        return out


def expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = list(grid)
    return [
        dict(zip(keys, values, strict=True))
        for values in itertools.product(*(grid[k] for k in keys))
    ]


def sweep(
    ctx: BacktestContext, base: Settings, grid: dict[str, list[Any]], start=None, end=None
) -> pd.DataFrame:
    """Run every parameter combination over the same period; one row of metrics each."""
    rows = []
    for combo in expand_grid(grid):
        settings = apply_overrides(base, combo) if combo else base
        m = result_metrics(ctx.run(settings, start, end))
        rows.append({**{k: str(v) for k, v in combo.items()}, **m})
    return pd.DataFrame(rows)
