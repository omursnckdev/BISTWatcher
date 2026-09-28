"""News classifiers: structured metadata only - never BUY/SELL decisions (spec §19, §68).

* :class:`RuleBasedClassifier` - deterministic Turkish KAP rules (subject + keywords).
  Free, fast, reproducible: the default, and what historical backtests use.
* :class:`ClaudeClassifier` - LLM classification via the Anthropic API with on-disk
  caching (see :mod:`bist_quant.news.llm`).
* :class:`HybridClassifier` - rules for routine/administrative disclosures, the LLM
  only for potentially material ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from bist_quant.models.news import EventType, ImpactHorizon, NewsArticle, NewsClassification
from bist_quant.news.parser import extract_amount


class NewsClassifier(Protocol):
    name: str

    async def classify(self, article: NewsArticle) -> NewsClassification: ...


@dataclass(frozen=True)
class Rule:
    event: EventType
    sentiment: float
    importance: float
    confidence: float
    horizon: ImpactHorizon = ImpactHorizon.MEDIUM


def _lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


# KAP subjects that carry no price-relevant information for a swing horizon.
ADMIN_SUBJECTS = (
    "genel kurul",
    "faaliyet raporu",
    "sorumluluk beyanı",
    "şirket genel bilgi formu",
    "esas sözleşme tadili",
    "bağımsız denetim",
    "kurumsal yönetim",
    "sürdürülebilirlik",
    "katılım finansı",
    "kayıtlı sermaye tavanı",
    "ihraç tavanı",
    "izahname",
    "piyasa yapıcılığı",
    "likidite sağlayıcı",
    "varant",
    "haftalık rapor",
    "yatırımcı ilişkileri",
    "halka arz fiyatının",
    "değerlendirme raporu",
    "bilgi formu",
)
DEBT_SUBJECTS = ("pay dışında sermaye piyasası aracı", "borçlanma aracı")
ADMIN_KEYWORDS = (
    "yatırımcı sunumu",
    "yatırımcı ilişkileri",
    "kaydileştirilmeyen",
    "fiili dolaşımdaki pay",
    "komite üyelik",
    "hak kullanım",
    "özel durumlar tebliği'nin",
    "ihraç belgesi",
    "yönetim kurulu komite",
    "temerrüt",  # settlement-default notices about an investor, not the company
)
# "yatırım" (investment) but not "yatırımcı" (investor)
_INVESTMENT = re.compile(r"yatırım(?!cı)")


class RuleBasedClassifier:
    """Deterministic classification of KAP disclosures (Turkish subject + keywords).

    Confidence is deliberately moderate: rules can recognise *what* happened but not
    always *how good* it is (e.g. earnings quality needs numbers or an LLM).
    """

    name = "rules"

    async def classify(self, article: NewsArticle) -> NewsClassification:
        return self.classify_sync(article)

    def classify_sync(self, article: NewsArticle) -> NewsClassification:
        # Keywords run on KAP's own subject + summary only: the full page text contains
        # navigation chrome ("Yatırımcı İlişkileri" ...) that would trigger false matches.
        # The body is used for amount extraction (and by the LLM classifier).
        subject = _lower(article.subject)
        text = _lower(article.summary)
        both = f"{subject} {text}"
        rule = self._rule(subject, text, both)
        amount = extract_amount(article.text or article.summary)
        return NewsClassification(
            event_type=rule.event,
            sentiment=rule.sentiment,
            importance=rule.importance,
            confidence=rule.confidence,
            impact_horizon=rule.horizon,
            summary=article.summary or article.subject,
            amount=amount[0] if amount else None,
            currency=amount[1] if amount else None,
            classifier=self.name,
        )

    @staticmethod
    def _rule(subject: str, text: str, both: str) -> Rule:  # noqa: C901 - flat rule table
        E, H = EventType, ImpactHorizon
        # Exchange measures on the stock itself
        if _has(
            both,
            "brüt takas",
            "tek fiyat",
            "kredili işlem",
            "açığa satış yasağı",
            "işlem sırası kapatma",
            "işlemlerin durdurulması",
            "emir paketi",
        ):
            return Rule(E.REGULATORY_ACTION, -0.7, 0.8, 0.8, H.SHORT)
        # SPK bans / measures on *investors* trading the stock (manipulation cases)
        if _has(both, "işlem yasağı", "tedbir kararı"):
            return Rule(E.REGULATORY_ACTION, -0.3, 0.4, 0.5, H.SHORT)
        if _has(both, "tipe dönüşüm"):
            return Rule(E.SHARE_CONVERSION, -0.2, 0.3, 0.6, H.SHORT)
        if _has(subject, "devre kesici"):
            return Rule(E.OTHER, 0.0, 0.0, 0.5, H.SHORT)
        if _has(subject, "yeni iş ilişkisi"):
            gov = _has(text, "savunma sanayi", "bakanlığı", "kamu", "belediye", "ihale")
            exp = _has(text, "ihracat", "yurt dışı")
            event = E.GOVERNMENT_CONTRACT if gov else E.EXPORT_DEAL if exp else E.NEW_CONTRACT
            return Rule(event, 0.6, 0.6, 0.7)
        if _has(subject, "kar payı", "kâr payı"):
            if _has(text, "dağıtılmaması", "dağıtmama", "dağıtılmayacak"):
                return Rule(E.DIVIDEND, -0.3, 0.4, 0.7)
            return Rule(E.DIVIDEND, 0.3, 0.4, 0.7)
        if _has(subject, "geri alın", "geri alım"):
            return Rule(E.SHARE_BUYBACK, 0.4, 0.4, 0.8, H.SHORT)
        if _has(subject, "sermaye artırımı", "sermaye azaltımı"):
            if _has(text, "bedelsiz", "iç kaynak"):
                return Rule(E.CAPITAL_INCREASE, 0.5, 0.6, 0.7)
            if _has(text, "bedelli", "rüçhan"):
                return Rule(E.CAPITAL_INCREASE, -0.3, 0.5, 0.6)
            return Rule(E.CAPITAL_INCREASE, 0.0, 0.3, 0.4)
        if _has(subject, "kredi derecelendirme"):
            if _has(text, "yükselt", "pozitif", "olumlu"):
                return Rule(E.CREDIT_RATING, 0.4, 0.4, 0.6)
            if _has(text, "düşür", "negatif", "olumsuz", "izlemeye al"):
                return Rule(E.CREDIT_RATING, -0.4, 0.4, 0.6)
            return Rule(E.CREDIT_RATING, 0.05, 0.2, 0.5)
        if _has(subject, "finansal rapor"):
            return Rule(E.EARNINGS, 0.0, 0.7, 0.3)
        if _has(subject, "pay alım satım"):
            text = text.replace("pay alım satım bildirimi", "")
            if _has(text, "satış", "satım") and not _has(text, "alış", "alım"):
                return Rule(E.INSIDER_TRANSACTION, -0.2, 0.3, 0.5, H.SHORT)
            if _has(text, "alış", "alım"):
                return Rule(E.INSIDER_TRANSACTION, 0.2, 0.3, 0.5, H.SHORT)
            return Rule(E.INSIDER_TRANSACTION, 0.0, 0.2, 0.4, H.SHORT)
        if _has(subject, *DEBT_SUBJECTS):
            if _has(text, "kupon", "itfa", "faiz oranı"):
                return Rule(E.ADMINISTRATIVE, 0.0, 0.0, 0.8)
            return Rule(E.DEBT_REFINANCING, 0.0, 0.1, 0.6)
        if _has(subject, *ADMIN_SUBJECTS) or _has(both, *ADMIN_KEYWORDS):
            return Rule(E.ADMINISTRATIVE, 0.0, 0.0, 0.8)

        # Free-form material disclosures (Özel Durum Açıklaması etc.): keywords
        if _has(
            text,
            "yangın",
            "patlama",
            "üretime ara",
            "üretimin durdur",
            "grev",
            "iş kazası",
            "arıza",
            "hasar",
        ):
            return Rule(E.PRODUCTION_ISSUE, -0.6, 0.7, 0.6, H.SHORT)
        if _has(text, "dava", "soruşturma", "idari para cezası", "ceza", "haciz", "icra"):
            return Rule(E.LAWSUIT, -0.4, 0.5, 0.5)
        if _has(text, "iflas", "konkordato"):
            return Rule(E.LAWSUIT, -0.9, 0.9, 0.8)
        if _has(text, "sözleşme", "sipariş", "ihale", "anlaşma imza"):
            if _has(text, "fesih", "iptal"):
                return Rule(E.NEW_CONTRACT, -0.4, 0.5, 0.5)
            gov = _has(text, "savunma sanayi", "bakanlığı", "kamu", "belediye", "ihale")
            exp = _has(text, "ihracat", "yurt dışı")
            event = E.GOVERNMENT_CONTRACT if gov else E.EXPORT_DEAL if exp else E.NEW_CONTRACT
            return Rule(event, 0.5, 0.5, 0.6)
        if _has(
            text,
            "birleşme",
            "devralma",
            "satın alın",
            "satın alma",
            "iktisap",
            "pay devri",
            "ortaklık",
        ):
            return Rule(E.MERGER_ACQUISITION, 0.3, 0.5, 0.5, H.LONG)
        if _has(text, "kapasite"):
            return Rule(E.CAPACITY_EXPANSION, 0.4, 0.5, 0.5, H.LONG)
        if _INVESTMENT.search(text) or _has(text, "tesis", "fabrika", "teşvik belgesi"):
            return Rule(E.INVESTMENT, 0.3, 0.4, 0.5, H.LONG)
        if _has(text, "beklenti", "hedef", "tahmin", "öngörü"):
            return Rule(E.GUIDANCE, 0.1, 0.5, 0.3)
        if _has(text, "istifa", "görevden ayrıl"):
            return Rule(E.MANAGEMENT_CHANGE, -0.1, 0.3, 0.5)
        if _has(text, "atan", "genel müdür", "yönetim kurulu üye"):
            return Rule(E.MANAGEMENT_CHANGE, 0.0, 0.2, 0.5)
        if _has(text, "kredi", "finansman", "tahvil", "sukuk", "borçlanma"):
            return Rule(E.DEBT_REFINANCING, 0.05, 0.2, 0.5)
        if _has(text, "trafik sonuçları", "satış rakamları", "üretim rakamları"):
            return Rule(E.OTHER, 0.0, 0.2, 0.3)
        return Rule(E.OTHER, 0.0, 0.1, 0.3)


# Subjects worth an LLM call in hybrid mode (everything else stays rule-based).
MATERIAL_EVENTS = {
    EventType.NEW_CONTRACT,
    EventType.GOVERNMENT_CONTRACT,
    EventType.EXPORT_DEAL,
    EventType.EARNINGS,
    EventType.GUIDANCE,
    EventType.MERGER_ACQUISITION,
    EventType.INVESTMENT,
    EventType.CAPACITY_EXPANSION,
    EventType.PRODUCTION_ISSUE,
    EventType.LAWSUIT,
    EventType.CAPITAL_INCREASE,
    EventType.OTHER,
}


class HybridClassifier:
    """Rules first; potentially material events are re-classified by the LLM."""

    name = "hybrid"

    def __init__(self, rules: RuleBasedClassifier, llm: NewsClassifier) -> None:
        self.rules = rules
        self.llm = llm

    async def classify(self, article: NewsArticle) -> NewsClassification:
        base = self.rules.classify_sync(article)
        if base.event_type not in MATERIAL_EVENTS:
            return base
        try:
            return await self.llm.classify(article)
        except Exception:  # noqa: BLE001 - never let the LLM break the pipeline
            return base


def needs_detail(classification: NewsClassification, events: set[EventType]) -> bool:
    return classification.event_type in events


def normalise_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
