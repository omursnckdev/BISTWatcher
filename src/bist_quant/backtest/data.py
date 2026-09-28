"""Historical data loading and feature/score caching for backtests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from bist_quant.config import Settings
from bist_quant.data.market_data import ISTANBUL_TZ, MarketDataProvider, fetch_many
from bist_quant.indicators.features import compute_features
from bist_quant.logging import get_logger, log_event
from bist_quant.regime.market_regime import compute_index_features
from bist_quant.scanner import ScanError, prepare_bars

log = get_logger(__name__)


@dataclass
class HistoricalData:
    """Prepared (cleaned, adjusted) bars for the universe and benchmarks."""

    bars: dict[str, pd.DataFrame]
    index_bars: pd.DataFrame
    benchmarks: dict[str, pd.Series]  # benchmark name -> close series
    start: pd.Timestamp
    end: pd.Timestamp
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def calendar(self) -> pd.DatetimeIndex:
        """Benchmark-index sessions inside [start, end]."""
        idx = self.index_bars.index
        return idx[(idx >= self.start) & (idx <= self.end)]


async def load_history(
    settings: Settings,
    symbols: list[str],
    provider: MarketDataProvider,
    start: date | None = None,
    end: date | None = None,
) -> HistoricalData:
    bt = settings.backtest
    start = start or bt.start
    now = datetime.now(ISTANBUL_TZ)
    end = end or bt.end or now.date()
    fetch_start = start - timedelta(days=bt.warmup_days)
    index = settings.market_regime.index
    secondary = settings.market_regime.secondary_index
    wanted = [index, *([secondary] if secondary else []), *symbols]
    frames, errors = await fetch_many(provider, wanted, fetch_start, end)
    if index not in frames or frames[index].empty:
        raise ScanError(f"benchmark {index} unavailable: {errors.get(index, 'no data')}")

    index_bars, _ = prepare_bars(frames.pop(index), index, settings, now)
    benchmarks = {index: index_bars["close"]}
    if secondary and secondary in frames:
        sec_bars, _ = prepare_bars(frames.pop(secondary), secondary, settings, now)
        benchmarks[secondary] = sec_bars["close"]

    skipped = {s: f"fetch failed: {e}" for s, e in errors.items() if s in symbols}
    bars: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        if sym not in frames:
            continue
        prepared, _ = prepare_bars(frames[sym], sym, settings, now)
        if len(prepared) < settings.data.min_history_bars:
            skipped[sym] = f"insufficient history ({len(prepared)} bars)"
            continue
        bars[sym] = prepared
    log_event(
        log,
        "history_loaded",
        symbols=len(bars),
        skipped=len(skipped),
        start=str(start),
        end=str(end),
    )
    return HistoricalData(
        bars=bars,
        index_bars=index_bars,
        benchmarks=benchmarks,
        start=pd.Timestamp(start),
        end=min(pd.Timestamp(end), index_bars.index[-1]),
        skipped=skipped,
    )


def _key(*parts: object) -> str:
    return json.dumps(
        [p.model_dump(mode="json") if hasattr(p, "model_dump") else p for p in parts],
        sort_keys=True,
        default=str,
    )


class FeatureCache:
    """Memoises features per indicator configuration (sweeps reuse them)."""

    def __init__(self, history: HistoricalData) -> None:
        self.history = history
        self._features: dict[str, dict[str, pd.DataFrame]] = {}
        self._index: dict[str, pd.DataFrame] = {}

    def features(self, settings: Settings) -> dict[str, pd.DataFrame]:
        key = _key(settings.indicators, settings.scoring.rules, settings.risk.resistance_lookback)
        if key not in self._features:
            bench = self.history.index_bars["close"]
            self._features[key] = {
                sym: compute_features(
                    bars,
                    settings.indicators,
                    settings.scoring.rules,
                    settings.risk,
                    benchmark_close=bench,
                )
                for sym, bars in self.history.bars.items()
            }
        return self._features[key]

    def index_features(self, settings: Settings) -> pd.DataFrame:
        key = _key(settings.indicators, settings.market_regime)
        if key not in self._index:
            self._index[key] = compute_index_features(
                self.history.index_bars, settings.indicators, settings.market_regime
            )
        return self._index[key]
