"""KAP -> classified, scaled, reaction-tagged :class:`NewsEvent` objects -> :class:`NewsBook`.

Two stages so that price-independent work (download, classification) is separate
from price-dependent work (reaction), keeping ingestion and signal generation apart:

1. :func:`load_news_events`  - sync KAP, parse, classify, fetch texts, size-scale
2. :func:`attach_reactions`  - news vs price reaction (needs bars) -> NewsBook
"""

from __future__ import annotations

import asyncio
from datetime import date, time, timedelta

import pandas as pd

from bist_quant.config import Settings
from bist_quant.data.fundamentals import MarketCapStore, value_on
from bist_quant.data.kap_data import KapClient, KapError, KapStore, sync_disclosures
from bist_quant.logging import get_logger, log_event
from bist_quant.models.news import EventType, NewsArticle, NewsClassification, NewsEvent
from bist_quant.news.classifier import HybridClassifier, NewsClassifier, RuleBasedClassifier
from bist_quant.news.parser import kap_row_to_articles
from bist_quant.news.reaction import ReactionCalculator
from bist_quant.news.scale import SCALED_EVENTS, scale_multiplier
from bist_quant.scoring.news_score import NewsBook

log = get_logger(__name__)
EUR_USD_FALLBACK = 1.08


def build_classifier(settings: Settings) -> NewsClassifier:
    cfg = settings.news
    rules = RuleBasedClassifier()
    if cfg.classifier == "rules":
        return rules
    from bist_quant.news.llm import ClaudeClassifier

    llm = ClaudeClassifier(
        model=cfg.llm.model,
        effort=cfg.llm.effort,
        cache_dir=settings.resolve_path(cfg.llm.cache_dir),
        max_text_chars=cfg.llm.max_text_chars,
        use_fallbacks=cfg.llm.use_fallbacks,
    )
    return llm if cfg.classifier == "claude" else HybridClassifier(rules, llm)


async def _classify_all(
    classifier: NewsClassifier, articles: list[NewsArticle], concurrency: int
) -> list[NewsClassification]:
    if isinstance(classifier, RuleBasedClassifier):
        return [classifier.classify_sync(a) for a in articles]
    sem = asyncio.Semaphore(concurrency)
    rules = RuleBasedClassifier()

    async def one(a: NewsArticle) -> NewsClassification:
        async with sem:
            try:
                return await classifier.classify(a)
            except Exception as exc:  # noqa: BLE001 - fall back to rules, never crash
                log_event(
                    log, "news_classify_failed", level=30, source_id=a.source_id, error=str(exc)
                )
                return rules.classify_sync(a)

    return list(await asyncio.gather(*(one(a) for a in articles)))


async def _fetch_details(
    client: KapClient | None, store: KapStore, articles: list[NewsArticle], limit: int
) -> int:
    """Attach cached / downloaded body text. Newest first, at most ``limit`` downloads."""
    todo = []
    for a in articles:
        cached = store.detail(int(a.source_id))
        if cached is not None:
            a.text = cached
        elif client is not None:
            todo.append(a)
    todo = sorted(todo, key=lambda a: a.published_at, reverse=True)[:limit]

    async def one(a: NewsArticle) -> None:
        try:
            text = await client.disclosure_text(int(a.source_id))
        except KapError:
            return
        store.save_detail(int(a.source_id), text)
        a.text = text

    await asyncio.gather(*(one(a) for a in todo))
    return len(todo)


async def _fx_eurtry(settings: Settings, start: date, end: date) -> pd.Series | None:
    from bist_quant.data.market_data import CachingProvider, YahooChartProvider

    inner = YahooChartProvider()
    provider = CachingProvider(
        inner, settings.resolve_path(settings.data.raw_dir) / "yahoo", settings.data.cache_ttl_hours
    )
    try:
        frame = await provider.get_daily_bars("EURTRY=X", start, end)
        return frame["close"] if len(frame) else None
    except Exception as exc:  # noqa: BLE001 - FX is optional
        log_event(log, "fx_fetch_failed", level=30, error=str(exc))
        return None
    finally:
        await inner.aclose()


async def _apply_scale(settings: Settings, events: list[NewsEvent], start: date, end: date) -> None:
    cfg = settings.news.scale
    targets = [
        e
        for e in events
        if e.classification.amount and e.classification.event_type in SCALED_EVENTS
    ]
    if not cfg.enabled or not targets:
        return
    store = MarketCapStore(settings.resolve_path(settings.data.raw_dir) / "isyatirim")
    caps = await store.fetch_many(sorted({e.symbol for e in targets}), start, end)
    eur = None
    if any(e.classification.currency == "EUR" for e in targets):
        eur = await _fx_eurtry(settings, start, end)
    for e in targets:
        cap = caps.get(e.symbol)
        if cap is None:
            continue
        when = pd.Timestamp(e.article.published_at.date())
        mcap = value_on(cap["market_cap_try"], when)
        usd = value_on(cap["usdtry"], when)
        c = e.classification
        amount = c.amount
        if c.currency == "USD" and usd:
            amount *= usd
        elif c.currency == "EUR":
            eur_rate = value_on(eur, when) if eur is not None else None
            amount *= eur_rate if eur_rate else (usd or 0) * EUR_USD_FALLBACK
        if mcap and amount:
            e.scale_multiplier = scale_multiplier(
                amount,
                mcap,
                cfg.reference_ratio,
                cfg.sensitivity,
                cfg.min_multiplier,
                cfg.max_multiplier,
            )


async def load_news_events(
    settings: Settings,
    symbols: list[str],
    start: date,
    end: date,
    classifier: NewsClassifier | None = None,
    kap_client: KapClient | None = None,
    sync: bool | None = None,
) -> dict[str, list[NewsEvent]]:
    """Stage 1: KAP events for ``symbols`` published in [start, end] (Istanbul dates)."""
    cfg = settings.news
    store = KapStore(settings.resolve_path(cfg.kap_dir))
    sync = cfg.sync if sync is None else sync
    own_client = kap_client is None and (sync or cfg.fetch_details)
    client = kap_client or (KapClient() if own_client else None)
    try:
        if sync and client is not None:
            try:
                stats = await sync_disclosures(client, store, symbols, start, end)
                log_event(log, "kap_sync", **stats)
            except KapError as exc:
                log_event(log, "kap_sync_failed", level=30, error=str(exc))
        universe = set(symbols)
        articles: list[NewsArticle] = []
        seen: set[str] = set()
        for row in store.rows(start, end):
            for art in kap_row_to_articles(row, universe):
                if art.source_id in seen:
                    continue
                seen.add(art.source_id)
                if start <= art.published_at.date() <= end:
                    articles.append(art)

        rules = RuleBasedClassifier()
        if cfg.fetch_details:
            wanted = set(cfg.detail_event_types)
            need = [a for a in articles if rules.classify_sync(a).event_type.value in wanted]
            fetched = await _fetch_details(client, store, need, cfg.detail_max_per_run)
            log_event(log, "kap_details", candidates=len(need), downloaded=fetched)

        classifier = classifier or build_classifier(settings)
        classes = await _classify_all(classifier, articles, cfg.llm.concurrency)
    finally:
        if own_client and client is not None:
            await client.aclose()

    quality = cfg.source_quality
    events: list[NewsEvent] = []
    for art, cls in zip(articles, classes, strict=True):
        if cls.event_type is EventType.ADMINISTRATIVE:
            continue
        for sym in art.symbols:
            events.append(
                NewsEvent(
                    symbol=sym,
                    article=art,
                    classification=cls,
                    source_quality=quality.get(art.source, 0.5),
                )
            )
    await _apply_scale(settings, events, start - timedelta(days=10), end)
    by_symbol: dict[str, list[NewsEvent]] = {}
    for e in events:
        by_symbol.setdefault(e.symbol, []).append(e)
    log_event(log, "news_events_loaded", articles=len(articles), events=len(events))
    return by_symbol


def attach_reactions(
    events: dict[str, list[NewsEvent]],
    features: dict[str, pd.DataFrame],
    benchmark_close: pd.Series,
    settings: Settings,
) -> NewsBook:
    """Stage 2: add news-vs-price reactions (spec §24) and return a scoring-ready book."""
    cfg = settings.news
    hh, mm = (int(x) for x in cfg.session_close_time.split(":"))
    for sym, evs in events.items():
        f = features.get(sym)
        if f is None or not cfg.reaction.enabled:
            continue
        calc = ReactionCalculator(f, benchmark_close, cfg.reaction, time(hh, mm))
        for e in evs:
            label, mult, known_at, implied = calc.evaluate(
                e.article.published_at, e.classification.sentiment
            )
            e.reaction, e.reaction_multiplier = label, mult
            e.reaction_known_at, e.implied_sentiment = known_at, implied
    return NewsBook(events, cfg)


def news_window(end: date, settings: Settings) -> tuple[date, date]:
    """Publication window needed to score signals up to ``end``."""
    days = int(settings.news.decay.max_age_days) + 3
    return end - timedelta(days=days), end
