"""Scanner: data -> features -> factors -> score -> risk filter -> signal.

Market-data ingestion (providers) is kept separate from signal generation: this
module only orchestrates the two and never talks to a vendor directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from bist_quant.config import Settings
from bist_quant.data.market_data import ISTANBUL_TZ, MarketDataProvider, fetch_many
from bist_quant.data.quality import (
    adjust_prices,
    check_freshness,
    clean_bars,
    drop_incomplete_session,
)
from bist_quant.indicators.features import compute_features
from bist_quant.logging import get_logger, log_event
from bist_quant.models.market import DataQualityReport
from bist_quant.models.signals import (
    ComponentScore,
    Explanation,
    RegimeSnapshot,
    SignalResult,
)
from bist_quant.regime.market_regime import classify_market, compute_index_features
from bist_quant.strategy.entry import buy_threshold
from bist_quant.strategy.evaluate import decide_row, score_row
from bist_quant.strategy.filters import data_filter

log = get_logger(__name__)

# Indicator values exposed on each SignalResult (and in JSON output).
EXPORTED_INDICATORS = [
    "close",
    "raw_close",
    "ema_fast",
    "ema_medium",
    "ema_long",
    "ema_slow",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper",
    "bb_mid",
    "bb_lower",
    "atr",
    "atr_pct",
    "adx",
    "plus_di",
    "minus_di",
    "volume_ratio",
    "avg_turnover",
    "roc",
]


class ScanError(RuntimeError):
    pass


@dataclass
class ScanResult:
    as_of: date
    provider: str
    regime: RegimeSnapshot
    signals: list[SignalResult]
    skipped: dict[str, str] = field(default_factory=dict)
    quality: dict[str, DataQualityReport] = field(default_factory=dict)
    features: dict[str, pd.DataFrame] = field(default_factory=dict)
    index_features: pd.DataFrame | None = None
    # Latest (delayed) traded prices, filled by callers that want them for display only.
    # Signals always use the last completed session close.
    live_quotes: dict = field(default_factory=dict)


def prepare_bars(
    raw: pd.DataFrame, symbol: str, settings: Settings, now: datetime | None
) -> tuple[pd.DataFrame, DataQualityReport]:
    bars, report = clean_bars(raw, symbol)
    if settings.data.exclude_incomplete_session:
        before = len(bars)
        bars = drop_incomplete_session(bars, now, settings.data.session_close_time)
        if len(bars) < before:
            report.issues.append("excluded today's incomplete session bar")
            report.bars = len(bars)
            report.last_date = bars.index[-1].date() if len(bars) else None
    if settings.data.use_adjusted_prices:
        bars = adjust_prices(bars)
    return bars, report


def evaluate_symbol(
    symbol: str,
    features: pd.DataFrame,
    report: DataQualityReport,
    regime: RegimeSnapshot,
    settings: Settings,
    extra_components: dict[str, ComponentScore] | None = None,
) -> SignalResult:
    """Score the latest bar of ``features`` and classify the signal."""
    row = features.iloc[-1]
    score, comps = score_row(row, regime.score, settings, extra_components)
    data_ok, data_notes = data_filter(report)
    decision = decide_row(row, score, comps, regime.regime, settings, data_ok)
    plan, liquidity_ok, signal = decision.plan, decision.liquidity_ok, decision.signal
    liq_notes, notes = decision.liquidity_notes, decision.notes

    explanation = Explanation(filters=data_notes + liq_notes + notes)
    for comp in comps.values():
        if comp.enabled:
            explanation.positive_factors.extend(comp.positive_factors)
            explanation.negative_factors.extend(comp.negative_factors)
            explanation.notes.extend(comp.notes)
    indicators = {
        k: (None if pd.isna(row.get(k)) else round(float(row[k]), 4))
        for k in EXPORTED_INDICATORS
        if k in row.index
    }
    result = SignalResult(
        symbol=symbol,
        date=features.index[-1].date(),
        score=score,
        signal=signal,
        market_regime=regime.regime,
        buy_threshold=buy_threshold(regime.regime, settings.strategy),
        components=comps,
        risk=plan,
        liquidity_ok=liquidity_ok,
        data_ok=data_ok,
        explanation=explanation,
        close=float(row["raw_close"]),
        indicators=indicators,
    )
    log_event(
        log,
        "signal_generated",
        symbol=symbol,
        score=score,
        signal=signal.value,
        regime=regime.regime.value,
        date=str(result.date),
    )
    return result


def _news_components(settings, symbols, features, index_bars, news_events, now, as_of):
    """News factor per symbol at the scan's information cutoff."""
    if not settings.news.enabled:
        return {}
    weight = settings.scoring.weights.news
    if news_events is None:
        from bist_quant.scoring.composite_score import disabled_component

        comp = disabled_component("news", weight, "KAP news unavailable for this scan")
        return {s: {"news": comp} for s in symbols}
    from bist_quant.news.pipeline import attach_reactions

    book = attach_reactions(news_events, features, index_bars["close"], settings)
    cutoff = book.cutoff_for(as_of) if as_of is not None else now
    return {s: {"news": book.component(s, cutoff, weight)} for s in symbols}


async def run_scan(
    settings: Settings,
    symbols: list[str],
    provider: MarketDataProvider,
    as_of: date | None = None,
    now: datetime | None = None,
    breadth_symbols: list[str] | None = None,
    news_events: dict | None = None,
) -> ScanResult:
    """Run a full scan of ``symbols``.

    ``as_of`` replays the scan on a past date using only data <= as_of.
    ``breadth_symbols`` is the universe used for market breadth (default: ``symbols``);
    pass the full configured universe so a symbol's score does not depend on which
    other symbols happen to be scanned.
    ``news_events`` (from :func:`bist_quant.news.pipeline.load_news_events`) enables
    the news factor; with ``news.enabled`` but no events the factor is unavailable.
    """
    now = now or datetime.now(ISTANBUL_TZ)
    if as_of is not None:
        # A historical scan treats the as-of date's session as complete.
        now = datetime.combine(as_of, datetime.max.time(), ISTANBUL_TZ)
    end = as_of or now.date()
    start = end - timedelta(days=settings.data.history_days)
    index_symbol = settings.market_regime.index

    breadth_symbols = list(breadth_symbols) if breadth_symbols is not None else list(symbols)
    to_fetch = list(dict.fromkeys([*symbols, *breadth_symbols]))
    frames, errors = await fetch_many(provider, [index_symbol, *to_fetch], start, end)
    if index_symbol not in frames or frames[index_symbol].empty:
        raise ScanError(
            f"benchmark {index_symbol} unavailable: {errors.get(index_symbol, 'no data')}"
        )

    index_bars, index_report = prepare_bars(frames.pop(index_symbol), index_symbol, settings, now)
    index_features = compute_index_features(index_bars, settings.indicators, settings.market_regime)
    calendar = index_bars.index
    index_report = check_freshness(
        index_report, index_bars, None, end, settings.data.max_data_age_days, 0
    )
    if index_report.is_stale:
        log_event(log, "benchmark_stale", level=30, issues=index_report.issues)

    requested = set(symbols)
    skipped: dict[str, str] = {s: f"fetch failed: {e}" for s, e in errors.items() if s in requested}
    quality: dict[str, DataQualityReport] = {index_symbol: index_report}
    features: dict[str, pd.DataFrame] = {}
    for sym in to_fetch:
        if sym not in frames:
            continue
        bars, report = prepare_bars(frames[sym], sym, settings, now)
        report = check_freshness(
            report,
            bars,
            calendar,
            end,
            settings.data.max_data_age_days,
            settings.data.max_missing_sessions,
        )
        quality[sym] = report
        if len(bars) < settings.data.min_history_bars:
            if sym not in requested:
                continue
            need = settings.data.min_history_bars
            skipped[sym] = f"insufficient history ({len(bars)} < {need} bars)"
            continue
        features[sym] = compute_features(
            bars,
            settings.indicators,
            settings.scoring.rules,
            settings.risk,
            benchmark_close=index_bars["close"],
        )

    # Breadth: share of fresh breadth-universe symbols closing above the medium EMA.
    fresh = [
        features[s].iloc[-1] for s in breadth_symbols if s in features and not quality[s].is_stale
    ]
    above = [r["close"] > r["ema_medium"] for r in fresh if not pd.isna(r["ema_medium"])]
    breadth = (
        100 * sum(above) / len(above)
        if len(above) >= settings.market_regime.min_breadth_symbols
        else None
    )

    regime = classify_market(
        index_features, settings.market_regime, settings.scoring.weights.market_regime, breadth
    )
    log_event(
        log, "market_regime", regime=regime.regime.value, date=str(regime.date), breadth_pct=breadth
    )

    extras = _news_components(settings, symbols, features, index_bars, news_events, now, as_of)
    signals = [
        evaluate_symbol(sym, features[sym], quality[sym], regime, settings, extras.get(sym))
        for sym in symbols
        if sym in features
    ]
    signals.sort(key=lambda s: (-s.score, s.symbol))
    for sym, why in skipped.items():
        log_event(log, "symbol_skipped", level=30, symbol=sym, reason=why)
    return ScanResult(
        as_of=regime.date,
        provider=provider.name,
        regime=regime,
        signals=signals,
        skipped=skipped,
        quality=quality,
        features={s: features[s] for s in symbols if s in features},
        index_features=index_features,
    )
