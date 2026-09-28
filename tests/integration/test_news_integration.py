"""News factor wired into the scanner and the backtest (offline, synthetic prices)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime

import pytest

from bist_quant.backtest.data import load_history
from bist_quant.backtest.research import run_event_study
from bist_quant.backtest.runner import BacktestContext
from bist_quant.config import apply_overrides
from bist_quant.data.market_data import ISTANBUL_TZ, SyntheticProvider
from bist_quant.models.news import EventType, NewsArticle, NewsClassification, NewsEvent
from bist_quant.scanner import run_scan

TZ = ISTANBUL_TZ


def ev(symbol: str, when: datetime, sentiment: float, sid: str) -> NewsEvent:
    a = NewsArticle(
        source="kap",
        source_id=sid,
        symbols=[symbol],
        published_at=when,
        subject="Yeni İş İlişkisi",
        summary="Sözleşme İmzalanması",
    )
    c = NewsClassification(
        event_type=EventType.NEW_CONTRACT, sentiment=sentiment, importance=0.9, confidence=1.0
    )
    return NewsEvent(symbol=symbol, article=a, classification=c)


def test_scan_uses_news_factor(settings):
    s = apply_overrides(settings, {"market_regime.min_breadth_symbols": 1})
    as_of = date(2025, 3, 31)
    events = {
        "THYAO": [ev("THYAO", datetime(2025, 3, 31, 10, tzinfo=TZ), 1.0, "1")],
        "ASELS": [ev("ASELS", datetime(2025, 3, 31, 10, tzinfo=TZ), -1.0, "2")],
    }
    res = asyncio.run(
        run_scan(
            s, ["THYAO", "ASELS", "BIMAS"], SyntheticProvider(), as_of=as_of, news_events=events
        )
    )
    news = {x.symbol: x.components["news"] for x in res.signals}
    assert all(c.enabled for c in news.values())
    assert news["THYAO"].points > 7.5 > news["ASELS"].points
    assert news["BIMAS"].points == pytest.approx(7.5) and news["BIMAS"].notes
    thy = next(x for x in res.signals if x.symbol == "THYAO")
    assert any("KAP" in r for r in thy.explanation.positive_factors)
    # News published after the as-of cutoff (18:15) must not count.
    late = {"THYAO": [ev("THYAO", datetime(2025, 3, 31, 20, tzinfo=TZ), 1.0, "3")]}
    res2 = asyncio.run(run_scan(s, ["THYAO"], SyntheticProvider(), as_of=as_of, news_events=late))
    assert res2.signals[0].components["news"].points == pytest.approx(7.5)


@pytest.fixture(scope="module")
def ctx_with_news(settings):
    s = apply_overrides(settings, {"liquidity.min_avg_turnover_try": 0})
    symbols = [
        "THYAO",
        "ASELS",
        "BIMAS",
        "GARAN",
        "KCHOL",
        "SISE",
        "EREGL",
        "TUPRS",
        "AKBNK",
        "PGSUS",
        "FROTO",
    ]
    hist = asyncio.run(
        load_history(s, symbols, SyntheticProvider(), date(2021, 1, 4), date(2022, 12, 30))
    )
    events = {}
    for i, sym in enumerate(symbols):
        events[sym] = [
            ev(
                sym,
                datetime(2021, m, 10 + (i % 5), 11, tzinfo=TZ),
                0.8 if m % 2 else -0.8,
                f"{sym}-{m}",
            )
            for m in range(2, 12)
        ]
    return BacktestContext(hist, events), s


def test_backtest_news_ablation_and_point_in_time(ctx_with_news):
    ctx, s = ctx_with_news
    with_news = ctx.scored(s)
    without = ctx.scored(apply_overrides(s, {"news.enabled": False}))
    sym = "THYAO"
    assert "news" in with_news.scores[sym] and "news" not in without.scores[sym]
    news = with_news.scores[sym]["news"]
    assert news.min() < 7.5 < news.max()
    # Before the first event the news factor is exactly neutral.
    assert news.loc[:"2021-02-09"].eq(7.5).all()
    r1, r2 = ctx.run(s), ctx.run(apply_overrides(s, {"news.enabled": False}))
    assert len(r1.trades) > 0 and len(r2.trades) > 0


def test_event_study(ctx_with_news):
    ctx, s = ctx_with_news
    study = run_event_study(ctx, s)
    assert study is not None and study.events > 50
    assert "count" in study.by_type.columns
