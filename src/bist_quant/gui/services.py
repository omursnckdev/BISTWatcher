"""Qt-free operations behind the desktop app. Each call is synchronous and is run
in a worker thread by the UI; all trading logic stays in the engine modules."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from bist_quant.config import Settings, apply_overrides, load_settings
from bist_quant.data.market_data import ISTANBUL_TZ, MarketDataProvider, build_provider
from bist_quant.data.universe import load_universe, normalize_symbol
from bist_quant.gui.paths import config_dir
from bist_quant.gui.prefs import Prefs
from bist_quant.models.signals import MarketRegime
from bist_quant.scanner import ScanResult, run_scan
from bist_quant.strategy.exit_check import ExitCheck, Holding, check_holding

UNIVERSES = ("BIST30", "BIST50", "BIST100", "CUSTOM")


def load_app_settings(home: Path, prefs: Prefs) -> Settings:
    """Bundled YAML (or the user's copies) + the preferences set in the app."""
    base = load_settings(config_dir(home))
    base = base.model_copy(update={"project_root": home})
    settings = apply_overrides(
        base, prefs.overrides(base.strategy.buy_threshold[MarketRegime.BULL])
    )
    # News needs real tickers: never for the synthetic demo data.
    settings.news.enabled = bool(prefs.news) and settings.data.provider != "synthetic"
    return settings


def universe_symbols(settings: Settings, prefs: Prefs) -> list[str]:
    if prefs.universe == "CUSTOM":
        symbols = [normalize_symbol(s) for s in prefs.custom_symbols if s.strip()]
        if symbols:
            return list(dict.fromkeys(symbols))
    name = prefs.universe if prefs.universe != "CUSTOM" else None
    return load_universe(settings.universe, name)


async def _close(provider: MarketDataProvider) -> None:
    inner = getattr(provider, "inner", provider)
    if hasattr(inner, "aclose"):
        await inner.aclose()


async def _news_events(settings: Settings, symbols: list[str], end: date) -> dict | None:
    from bist_quant.news.pipeline import load_news_events, news_window

    try:
        return await load_news_events(settings, symbols, *news_window(end, settings))
    except Exception:  # noqa: BLE001 - news must never block a scan
        return None


def scan(
    settings: Settings,
    symbols: list[str],
    breadth: list[str],
    as_of: date | None = None,
    refresh: bool = False,
) -> ScanResult:
    """Same pipeline as ``python -m bist_quant tara``."""

    async def go() -> ScanResult:
        provider = build_provider(settings)
        if refresh and hasattr(provider, "force_refresh"):
            provider.force_refresh = True
        events = None
        if symbols and settings.news.enabled:
            end = as_of or datetime.now(ISTANBUL_TZ).date()
            events = await _news_events(settings, symbols, end)
        try:
            return await run_scan(
                settings,
                symbols,
                provider,
                as_of=as_of,
                breadth_symbols=breadth,
                news_events=events,
            )
        finally:
            await _close(provider)

    return asyncio.run(go())


@dataclass
class PortfolioReport:
    scan: ScanResult
    checks: list[ExitCheck]


def check_portfolio(
    settings: Settings, holdings: list[Holding], breadth: list[str]
) -> PortfolioReport:
    """Score the held symbols and replay the exit rules from each entry date."""
    symbols = list(dict.fromkeys(normalize_symbol(h.symbol) for h in holdings))
    oldest = min((h.entry_date for h in holdings), default=date.today())
    need = (datetime.now(ISTANBUL_TZ).date() - oldest).days + 400
    if need > settings.data.history_days:
        settings = apply_overrides(settings, {"data.history_days": need})
    result = scan(settings, symbols, breadth)
    checks = [
        check_holding(h, result.features.get(normalize_symbol(h.symbol)), settings)
        for h in holdings
    ]
    return PortfolioReport(result, checks)


def _bench_name(name: str) -> str:
    return name.replace(" buy&hold", " al-tut").replace(
        "Equal-weight universe", "Eşit ağırlıklı evren"
    )


@dataclass
class BacktestSummary:
    universe: str
    symbols: int
    start: date
    end: date
    metrics: dict
    benchmark_metrics: dict[str, dict]
    equity: pd.Series
    benchmarks: dict[str, pd.Series]
    trades: pd.DataFrame
    yearly: pd.DataFrame
    exit_reasons: pd.DataFrame
    skipped: dict[str, str] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


def backtest(
    settings: Settings,
    symbols: list[str],
    universe: str,
    start: date | None = None,
    end: date | None = None,
) -> BacktestSummary:
    """Same simulation as ``python -m bist_quant geritest``."""
    from bist_quant.backtest.data import load_history
    from bist_quant.backtest.metrics import (
        equity_metrics,
        exit_reason_breakdown,
        result_metrics,
        yearly_returns,
    )
    from bist_quant.backtest.runner import BacktestContext

    async def history():
        provider = build_provider(settings)
        try:
            return await load_history(settings, symbols, provider, start, end)
        finally:
            await _close(provider)

    hist = asyncio.run(history())
    events = None
    if settings.news.enabled:
        from bist_quant.news.pipeline import load_news_events

        news_start = hist.start.date() - timedelta(days=int(settings.news.decay.max_age_days) + 3)
        try:
            events = asyncio.run(
                load_news_events(settings, list(hist.bars), news_start, hist.end.date())
            )
        except Exception:  # noqa: BLE001 - run without news rather than fail
            events = None
    ctx = BacktestContext(hist, events)
    result = ctx.run(settings)
    if not len(result.equity):
        raise ValueError("Seçilen dönemde simüle edilecek seans yok.")
    initial = settings.backtest.initial_equity
    benches = {_bench_name(k): v for k, v in ctx.benchmarks(result.equity.index, initial).items()}
    rf = settings.backtest.risk_free_rate_annual_pct
    yearly = pd.DataFrame({"Strateji": yearly_returns(result.equity)})
    for name, series in benches.items():
        yearly[name] = yearly_returns(series)
    return BacktestSummary(
        universe=universe,
        symbols=len(hist.bars),
        start=result.equity.index[0].date(),
        end=result.equity.index[-1].date(),
        metrics=result_metrics(result),
        benchmark_metrics={n: equity_metrics(s, rf) for n, s in benches.items()},
        equity=result.equity,
        benchmarks=benches,
        trades=result.trades_frame(),
        yearly=yearly,
        exit_reasons=exit_reason_breakdown(result.trades) if result.trades else pd.DataFrame(),
        skipped=hist.skipped,
        stats=dict(result.stats),
    )
