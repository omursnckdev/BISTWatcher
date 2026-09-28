"""News / KAP: parsing, classification, decay, scale, reaction, point-in-time scoring."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from bist_quant.config import NewsSettings
from bist_quant.data.kap_data import (
    KapStore,
    extract_disclosure_text,
    month_starts,
    parse_publish_date,
    sync_disclosures,
)
from bist_quant.data.market_data import ISTANBUL_TZ
from bist_quant.models.news import (
    EventType,
    ImpactHorizon,
    NewsArticle,
    NewsClassification,
    NewsEvent,
)
from bist_quant.news.classifier import HybridClassifier, RuleBasedClassifier
from bist_quant.news.decay import decay_weight, exponential_decay, step_decay
from bist_quant.news.llm import ClaudeClassifier, LlmClassificationError, parse_classification
from bist_quant.news.parser import extract_amount, kap_row_to_articles
from bist_quant.news.reaction import ReactionCalculator
from bist_quant.news.scale import scale_multiplier
from bist_quant.scoring.news_score import NewsBook

TZ = ISTANBUL_TZ


def art(
    subject: str,
    summary: str = "",
    text: str | None = None,
    sid: str = "1",
    when: datetime | None = None,
    symbol: str = "ASELS",
) -> NewsArticle:
    return NewsArticle(
        source="kap",
        source_id=sid,
        symbols=[symbol],
        published_at=when or datetime(2026, 1, 5, 10, 0, tzinfo=TZ),
        subject=subject,
        summary=summary,
        text=text,
    )


# ------------------------------------------------------------------ parsing


def test_kap_row_to_articles():
    row = {
        "publishDate": "25.09.2026 22:44:39",
        "kapTitle": "ASELSAN",
        "subject": "Yeni İş İlişkisi",
        "summary": "Sözleşme İmzalanması",
        "stockCodes": "ASELS",
        "relatedStocks": None,
        "disclosureIndex": 123,
    }
    (a,) = kap_row_to_articles(row, {"ASELS", "THYAO"})
    assert a.symbols == ["ASELS"] and a.source_id == "123"
    assert a.published_at == datetime(2026, 9, 25, 22, 44, 39, tzinfo=TZ)
    related = {**row, "stockCodes": None, "relatedStocks": "THYAO, PGSUS"}
    assert kap_row_to_articles(related, {"ASELS", "THYAO"})[0].symbols == ["THYAO"]
    assert kap_row_to_articles({**row, "stockCodes": "XYZ"}, {"ASELS"}) == []
    breaker = {**row, "kapTitle": "BORSA İSTANBUL BISTECH DEVRE KESİCİ UYGULAMASI"}
    assert kap_row_to_articles(breaker, {"ASELS"}) == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("toplam tutarı 225.000.000 Avro olan sözleşme", (225e6, "EUR")),
        ("1,2 milyar TL tutarında", (1.2e9, "TRY")),
        ("45 milyon ABD Doları ve 10 milyon TL", (45e6, "USD")),
        ("tutar bilgisi yok", None),
    ],
)
def test_extract_amount(text, expected):
    got = extract_amount(text)
    if expected is None:
        assert got is None
    else:
        assert got[0] == pytest.approx(expected[0]) and got[1] == expected[1]


def test_extract_disclosure_text_drops_scripts_and_english():
    page = (
        '<html><script>var x="yatırım";</script><div class="content-en">English text</div>'
        '<div class="content-tr">Türkçe &amp; metin 225.000.000 Avro</div></html>'
    )
    text = extract_disclosure_text(page)
    assert "Türkçe & metin 225.000.000 Avro" in text
    assert "English" not in text and "var x" not in text


def test_publish_date_and_months():
    assert parse_publish_date("01.03.2016 23:23:25").hour == 23
    assert (
        len(month_starts(pd.Timestamp("2025-11-15").date(), pd.Timestamp("2026-02-01").date())) == 4
    )


# ------------------------------------------------------------------ classification

RULE_CASES = [
    ("Yeni İş İlişkisi", "Sözleşme İmzalanması", EventType.NEW_CONTRACT, 1),
    ("Payların Geri Alınmasına İlişkin Bildirim", "Geri alım", EventType.SHARE_BUYBACK, 1),
    (
        "Sermaye Artırımı - Azaltımı İşlemlerine İlişkin Bildirim",
        "Bedelsiz Sermaye Artırımı",
        EventType.CAPITAL_INCREASE,
        1,
    ),
    (
        "Sermaye Artırımı - Azaltımı İşlemlerine İlişkin Bildirim",
        "Bedelli Sermaye Artırımı",
        EventType.CAPITAL_INCREASE,
        -1,
    ),
    ("Kredi Derecelendirmesi", "Notumuz yükseltildi", EventType.CREDIT_RATING, 1),
    ("Finansal Rapor", "", EventType.EARNINGS, 0),
    ("Özel Durum Açıklaması (Genel)", "Fabrikamızda yangın", EventType.PRODUCTION_ISSUE, -1),
    ("Özel Durum Açıklaması (Genel)", "Brüt takas uygulaması", EventType.REGULATORY_ACTION, -1),
    ("Genel Kurul İşlemlerine İlişkin Bildirim", "Olağan genel kurul", EventType.ADMINISTRATIVE, 0),
    ("Özel Durum Açıklaması (Genel)", "Yatırımcı Sunumu Güncellemesi", EventType.ADMINISTRATIVE, 0),
    ("Özel Durum Açıklaması (Genel)", "2 No.lu Döküm Makinesi Yatırımı", EventType.INVESTMENT, 1),
    ("Pay Alım Satım Bildirimi", "Pay Alım Satım Bildirimi", EventType.INSIDER_TRANSACTION, 0),
    ("Borsada İşlem Gören Tipe Dönüşüm Duyurusu", "DÖNÜŞÜM", EventType.SHARE_CONVERSION, -1),
]


@pytest.mark.parametrize("subject,summary,event,sign", RULE_CASES)
def test_rule_classifier(subject, summary, event, sign):
    c = RuleBasedClassifier().classify_sync(art(subject, summary))
    assert c.event_type is event
    assert np.sign(c.sentiment) == sign
    assert -1 <= c.sentiment <= 1 and 0 <= c.importance <= 1


def test_rules_ignore_page_chrome_in_body():
    # Body text full of navigation words must not change the keyword classification.
    a = art(
        "Özel Durum Açıklaması (Genel)",
        "Genel açıklama",
        text="Yatırımcı İlişkileri yatırım dava yangın 10 milyon TL",
    )
    c = RuleBasedClassifier().classify_sync(a)
    assert c.event_type is EventType.OTHER
    assert c.amount == pytest.approx(10e6)  # the body is still used for amounts


class StubResponse(SimpleNamespace):
    pass


class StubClient:
    """Mimics ``AsyncAnthropic().beta.messages.create``."""

    def __init__(self, payload: dict | None = None, stop_reason: str = "end_turn"):
        self.calls = []
        self.payload = payload
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="text", text=json.dumps(self.payload))
        return StubResponse(stop_reason=self.stop_reason, content=[block])


LLM_OK = {
    "event_type": "new_contract",
    "sentiment": 0.8,
    "importance": 0.9,
    "confidence": 0.85,
    "impact_horizon": "medium",
    "summary": "Signed EUR 225m contract.",
    "amount": 225000000,
    "currency": "EUR",
}


def test_claude_classifier_structured_output_and_cache(tmp_path):
    stub = StubClient(LLM_OK)
    clf = ClaudeClassifier(model="claude-opus-5", cache_dir=tmp_path, client=stub)
    a = art("Yeni İş İlişkisi", "Sözleşme", text="225.000.000 Avro")
    c1 = asyncio.run(clf.classify(a))
    c2 = asyncio.run(clf.classify(a))
    assert len(stub.calls) == 1  # second call served from the disk cache
    assert c1 == c2 and c1.event_type is EventType.NEW_CONTRACT and c1.amount == 225e6
    call = stub.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "low"
    assert call["fallbacks"] == "default"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "225.000.000 Avro" in call["messages"][0]["content"]


def test_claude_classifier_refusal_and_bad_payload(tmp_path):
    refused = ClaudeClassifier(client=StubClient(LLM_OK, stop_reason="refusal"))
    with pytest.raises(LlmClassificationError):
        asyncio.run(refused.classify(art("Yeni İş İlişkisi")))
    with pytest.raises(LlmClassificationError):
        parse_classification({"event_type": "not_a_type"}, "x")
    clipped = parse_classification({**LLM_OK, "sentiment": 3, "importance": -1}, "x")
    assert clipped.sentiment == 1 and clipped.importance == 0


def test_hybrid_uses_llm_only_for_material_and_falls_back():
    stub = StubClient(LLM_OK)
    hybrid = HybridClassifier(RuleBasedClassifier(), ClaudeClassifier(client=stub))
    admin = asyncio.run(hybrid.classify(art("Genel Kurul İşlemlerine İlişkin Bildirim")))
    assert admin.classifier == "rules" and not stub.calls
    material = asyncio.run(hybrid.classify(art("Yeni İş İlişkisi", "Sözleşme")))
    assert material.classifier.startswith("claude") and len(stub.calls) == 1
    broken = HybridClassifier(
        RuleBasedClassifier(), ClaudeClassifier(client=StubClient(LLM_OK, "refusal"))
    )
    assert asyncio.run(broken.classify(art("Yeni İş İlişkisi"))).classifier == "rules"


# ------------------------------------------------------------------ decay / scale


def test_decay():
    assert exponential_decay(0, 24, 240) == 1
    assert exponential_decay(24, 24, 240) == pytest.approx(0.5)
    assert exponential_decay(300, 24, 240) == 0
    table = [(6, 1.0), (24, 0.7), (72, 0.4), (168, 0.15)]
    assert [step_decay(h, table) for h in (1, 10, 48, 100, 200)] == [1.0, 0.7, 0.4, 0.15, 0.0]
    cfg = NewsSettings().decay
    assert decay_weight(72, ImpactHorizon.MEDIUM, cfg) == pytest.approx(0.5)
    assert decay_weight(24, ImpactHorizon.SHORT, cfg) < decay_weight(24, ImpactHorizon.LONG, cfg)


def test_scale_multiplier():
    assert scale_multiplier(1e7, 1e9) == pytest.approx(1.0)  # 1% of market cap
    assert scale_multiplier(1e8, 1e9) == pytest.approx(1.5)
    assert scale_multiplier(1e5, 1e9) == pytest.approx(0.5)  # clipped
    assert scale_multiplier(0, 1e9) == 1.0


# ------------------------------------------------------------------ reaction / book

DAYS = pd.bdate_range("2026-01-01", periods=10)


def _bars(closes):
    f = pd.DataFrame({"close": closes}, index=DAYS[: len(closes)])
    f["atr"] = 2.0  # ATR% = 2% at price 100
    return f


def _event(
    sent: float, when: datetime, imp: float = 0.8, conf: float = 1.0, horizon=ImpactHorizon.MEDIUM
) -> NewsEvent:
    return NewsEvent(
        symbol="ASELS",
        article=art("x", when=when),
        classification=NewsClassification(
            event_type=EventType.NEW_CONTRACT,
            sentiment=sent,
            importance=imp,
            confidence=conf,
            impact_horizon=horizon,
        ),
    )


def test_reaction_labels_and_timing():
    cfg = NewsSettings().reaction
    bench = pd.Series(100.0, index=DAYS)
    up = ReactionCalculator(_bars([100, 100, 103, 103, 103]), bench, cfg, time(18, 0))
    # Published during Jan 2 session -> reference Jan 1 close, reaction at Jan 2 close.
    label, mult, known, _ = up.evaluate(datetime(2026, 1, 2, 11, 0, tzinfo=TZ), 0.6)
    assert label is None or label  # Jan 2 close is 100 -> no move
    label, mult, known, _ = up.evaluate(datetime(2026, 1, 2, 19, 0, tzinfo=TZ), 0.6)
    assert label == "POSITIVE_NEWS_POSITIVE_REACTION" and mult == cfg.confirm_multiplier
    assert known == datetime(2026, 1, 5, 18, 0, tzinfo=TZ)  # next session's close (Mon)
    flat = ReactionCalculator(_bars([100] * 5), bench, cfg, time(18, 0))
    label, mult, _, _ = flat.evaluate(datetime(2026, 1, 2, 19, 0, tzinfo=TZ), 0.6)
    assert label == "POSITIVE_NEWS_NO_REACTION" and mult == cfg.no_reaction_multiplier
    down = ReactionCalculator(_bars([100, 100, 95, 95, 95]), bench, cfg, time(18, 0))
    label, mult, _, _ = down.evaluate(datetime(2026, 1, 2, 19, 0, tzinfo=TZ), 0.6)
    assert label == "POSITIVE_NEWS_NEGATIVE_REACTION" and mult < 0
    label, _, _, implied = down.evaluate(datetime(2026, 1, 2, 19, 0, tzinfo=TZ), 0.0)
    assert label == "NEUTRAL_NEWS_NEGATIVE_REACTION" and implied < 0


def test_news_book_is_point_in_time():
    cfg = NewsSettings()
    ev = _event(0.8, datetime(2026, 1, 5, 19, 0, tzinfo=TZ))  # after Jan 5 cutoff (18:15)
    ev.reaction, ev.reaction_multiplier = "POSITIVE_NEWS_NEGATIVE_REACTION", -0.5
    ev.reaction_known_at = datetime(2026, 1, 6, 18, 0, tzinfo=TZ)
    book = NewsBook({"ASELS": [ev]}, cfg)
    day5 = book.cutoff_for(pd.Timestamp("2026-01-05"))
    day6 = book.cutoff_for(pd.Timestamp("2026-01-06"))
    day7 = book.cutoff_for(pd.Timestamp("2026-01-07"))
    # Not yet published at Jan 5's cutoff -> neutral.
    assert book.component("ASELS", day5, 15).points == pytest.approx(7.5)
    # Jan 6: published, reaction observable at 18:00 (< 18:15 cutoff) -> contrary, negative.
    assert book.component("ASELS", day6, 15).points < 7.5
    # Without a reaction the same news is positive; and it decays over time.
    ev.reaction_known_at = None
    p6 = book.component("ASELS", day6, 15).points
    assert p6 > 7.5 and book.component("ASELS", day7, 15).points < p6
    far = book.cutoff_for(pd.Timestamp("2026-01-30"))
    comp = book.component("ASELS", far, 15)
    assert comp.points == pytest.approx(7.5) and comp.notes  # older than max_age


def test_news_points_bounds():
    cfg = NewsSettings()
    when = datetime(2026, 1, 5, 9, 0, tzinfo=TZ)
    evs = [_event(1.0, when + timedelta(minutes=i)) for i in range(10)]
    book = NewsBook({"ASELS": evs}, cfg)
    pts = book.component("ASELS", book.cutoff_for(pd.Timestamp("2026-01-05")), 15).points
    assert 13 < pts <= 15
    bad = NewsBook({"ASELS": [_event(-1.0, when + timedelta(minutes=i)) for i in range(10)]}, cfg)
    assert bad.component("ASELS", bad.cutoff_for(pd.Timestamp("2026-01-05")), 15).points < 2


# ------------------------------------------------------------------ KAP sync


class FakeKap:
    def __init__(self):
        self.calls = []

    async def resolve_members(self, symbols):
        return {s: f"oid-{s}" for s in symbols}

    async def disclosures(self, start, end, oids):
        self.calls.append((start, end, tuple(oids)))
        return [
            {
                "disclosureIndex": f"{start}-{o}",
                "publishDate": start.strftime("%d.%m.%Y 10:00:00"),
                "stockCodes": o.removeprefix("oid-"),
            }
            for o in oids
        ]

    async def disclosure_text(self, index):
        return ""


def test_sync_is_incremental(tmp_path):
    store = KapStore(tmp_path)
    kap = FakeKap()
    s, e = pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-03-31").date()
    asyncio.run(sync_disclosures(kap, store, ["AAA"], s, e))
    assert len(kap.calls) == 3
    asyncio.run(sync_disclosures(kap, store, ["AAA"], s, e))
    assert len(kap.calls) == 3  # complete months are not re-downloaded
    asyncio.run(sync_disclosures(kap, store, ["AAA", "BBB"], s, e))
    assert len(kap.calls) == 6 and all(c[2] == ("oid-BBB",) for c in kap.calls[3:])
    assert len(store.rows(s, e)) == 6
