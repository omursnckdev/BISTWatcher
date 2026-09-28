"""News / KAP domain models (spec §19-24, §60, §69)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class EventType(StrEnum):
    NEW_CONTRACT = "new_contract"
    EARNINGS = "earnings"
    GUIDANCE = "guidance"
    DIVIDEND = "dividend"
    CAPITAL_INCREASE = "capital_increase"
    SHARE_BUYBACK = "share_buyback"
    MERGER_ACQUISITION = "merger_acquisition"
    INVESTMENT = "investment"
    CAPACITY_EXPANSION = "capacity_expansion"
    PRODUCTION_ISSUE = "production_issue"
    REGULATORY_ACTION = "regulatory_action"
    LAWSUIT = "lawsuit"
    MANAGEMENT_CHANGE = "management_change"
    CREDIT_RATING = "credit_rating"
    DEBT_REFINANCING = "debt_refinancing"
    EXPORT_DEAL = "export_deal"
    GOVERNMENT_CONTRACT = "government_contract"
    INSIDER_TRANSACTION = "insider_transaction"
    SHARE_CONVERSION = "share_conversion"
    MACRO_EVENT = "macro_event"
    SECTOR_EVENT = "sector_event"
    ADMINISTRATIVE = "administrative"
    OTHER = "other"


class ImpactHorizon(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class NewsArticle(BaseModel):
    """A raw news item / disclosure, as ingested (before classification)."""

    source: str  # "kap", "rss:<feed>", ...
    source_id: str  # disclosure index / article id (unique within source)
    symbols: list[str]  # universe tickers the item refers to
    published_at: datetime  # timezone-aware (Europe/Istanbul for KAP)
    company: str | None = None
    subject: str = ""  # KAP subject (bildirim konusu) / headline
    summary: str = ""
    text: str | None = None  # full body when fetched
    url: str | None = None
    raw: dict = Field(default_factory=dict)


class NewsClassification(BaseModel):
    """Structured metadata produced by a classifier. Never a trade instruction."""

    event_type: EventType
    sentiment: float = Field(ge=-1.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    impact_horizon: ImpactHorizon = ImpactHorizon.MEDIUM
    summary: str = ""
    amount: float | None = Field(default=None, ge=0)  # contract/deal value, if stated
    currency: str | None = None  # TRY | USD | EUR
    classifier: str = "rules"


class NewsEvent(BaseModel):
    """A classified article for one symbol, with the adjustments used in scoring."""

    symbol: str
    article: NewsArticle
    classification: NewsClassification
    source_quality: float = Field(1.0, ge=0.0, le=1.0)
    scale_multiplier: float = 1.0  # company-size adjustment (spec §23)
    reaction: str | None = None  # e.g. POSITIVE_NEWS_NO_REACTION (spec §24)
    reaction_multiplier: float = 1.0
    reaction_known_at: datetime | None = None  # when the reaction became observable
    implied_sentiment: float | None = None  # neutral news: sentiment implied by the reaction

    @property
    def signed_impact(self) -> float:
        c = self.classification
        return (
            c.sentiment * c.importance * c.confidence * self.source_quality * self.scale_multiplier
        )
