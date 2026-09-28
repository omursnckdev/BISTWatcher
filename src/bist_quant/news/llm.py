"""LLM news classification via the Anthropic API (optional; ``pip install bist-quant[llm]``).

The model returns **metadata only** (event type, sentiment, importance, ...). It never
sees prices or portfolio state and cannot emit trade instructions - scoring and
signals stay rule-based (spec §19, §81.11).

Every response is cached on disk under ``<cache_dir>/<model>/<prompt-version>/`` keyed
by the article id, so re-runs and backtests never pay twice for the same disclosure.
Credentials come from the environment (``ANTHROPIC_API_KEY`` or an ``ant auth login``
profile) - never from config files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bist_quant.logging import get_logger, log_event
from bist_quant.models.news import EventType, ImpactHorizon, NewsArticle, NewsClassification

log = get_logger(__name__)

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = f"""You classify Turkish public company disclosures (KAP - Kamuyu \
Aydınlatma Platformu) and financial news about Borsa İstanbul companies.

Return structured metadata describing the event. You do NOT give investment advice and \
you do NOT say whether to buy or sell - downstream code decides that.

Fields:
- event_type: one of {", ".join(e.value for e in EventType)}.
  Use "administrative" for routine filings with no price relevance (general assembly \
paperwork, governance forms, periodic reports without new information).
- sentiment: expected direction of the effect on the company's share price, -1.0 \
(strongly negative) to +1.0 (strongly positive); 0 when neutral or unclear.
- importance: 0.0-1.0, how material the event is for the company's value, relative to \
the company's size when the text allows (a contract worth a large share of annual \
revenue is highly important; a routine monthly statistic is not).
- confidence: 0.0-1.0, how sure you are of sentiment and importance given the text.
- impact_horizon: "short" (days), "medium" (weeks), "long" (months+).
- summary: one short English sentence stating the facts.
- amount / currency: the main monetary value stated (contract, deal, investment), as a \
plain number in units (225.000.000 Avro -> 225000000, "EUR"); null when none. \
currency is one of TRY, USD, EUR.

Guidance for Borsa İstanbul practice: bonus issues (bedelsiz sermaye artırımı) are \
usually received positively, rights issues (bedelli) negatively; share-buyback \
notices are mildly positive; SPK/Borsa measures (tedbir, brüt takas, işlem yasağı) \
are negative; conversion of shares to tradable form (tipe dönüşüm) signals potential \
supply. For financial reports judge profitability and growth versus the previous \
period when figures are present; otherwise keep confidence low."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "event_type": {"type": "string", "enum": [e.value for e in EventType]},
        "sentiment": {"type": "number"},
        "importance": {"type": "number"},
        "confidence": {"type": "number"},
        "impact_horizon": {"type": "string", "enum": [h.value for h in ImpactHorizon]},
        "summary": {"type": "string"},
        "amount": {"type": ["number", "null"]},
        "currency": {"type": ["string", "null"], "enum": ["TRY", "USD", "EUR", None]},
    },
    "required": [
        "event_type",
        "sentiment",
        "importance",
        "confidence",
        "impact_horizon",
        "summary",
        "amount",
        "currency",
    ],
    "additionalProperties": False,
}


class LlmClassificationError(RuntimeError):
    pass


def _clip(x: float, lo: float, hi: float) -> float:
    return min(max(float(x), lo), hi)


def parse_classification(data: dict, classifier: str) -> NewsClassification:
    """Validate model JSON into a NewsClassification (numeric fields clipped to range)."""
    try:
        return NewsClassification(
            event_type=EventType(data["event_type"]),
            sentiment=_clip(data["sentiment"], -1, 1),
            importance=_clip(data["importance"], 0, 1),
            confidence=_clip(data["confidence"], 0, 1),
            impact_horizon=ImpactHorizon(data.get("impact_horizon") or "medium"),
            summary=str(data.get("summary") or "")[:500],
            amount=None if data.get("amount") in (None, "") else abs(float(data["amount"])),
            currency=data.get("currency"),
            classifier=classifier,
        )
    except (KeyError, ValueError, TypeError, ValidationError) as exc:
        raise LlmClassificationError(f"invalid classification payload: {exc}") from exc


def article_prompt(article: NewsArticle, max_chars: int) -> str:
    body = (article.text or "")[:max_chars]
    parts = [
        f"Company: {article.company or '-'} ({', '.join(article.symbols)})",
        f"Published: {article.published_at.isoformat()}",
        f"KAP subject: {article.subject}",
        f"Summary: {article.summary}",
    ]
    if body:
        parts.append(f"Text:\n{body}")
    return "\n".join(parts)


class ClaudeClassifier:
    """Anthropic Messages API classifier with JSON-schema structured output."""

    def __init__(
        self,
        model: str = "claude-opus-5",
        effort: str = "low",
        cache_dir: Path | None = None,
        max_text_chars: int = 6000,
        client: Any | None = None,
        use_fallbacks: bool = True,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_text_chars = max_text_chars
        self.name = f"claude:{model}"
        self.use_fallbacks = use_fallbacks
        self.cache_dir = Path(cache_dir) / model / PROMPT_VERSION if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise LlmClassificationError(
                    "the 'anthropic' package is required: pip install -e '.[llm]'"
                ) from exc
            client = anthropic.AsyncAnthropic()
        self.client = client

    def _cache_path(self, article: NewsArticle) -> Path | None:
        if not self.cache_dir:
            return None
        key = f"{article.source}-{article.source_id}"
        if not article.source_id:
            key = hashlib.sha256(article_prompt(article, self.max_text_chars).encode()).hexdigest()
        return self.cache_dir / f"{key}.json"

    async def classify(self, article: NewsArticle) -> NewsClassification:
        path = self._cache_path(article)
        if path and path.exists():
            return parse_classification(json.loads(path.read_text("utf-8")), self.name)
        data = await self._call(article)
        result = parse_classification(data, self.name)
        if path:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        log_event(
            log,
            "news_classified",
            classifier=self.name,
            source_id=article.source_id,
            event_type=result.event_type.value,
            sentiment=result.sentiment,
        )
        return result

    async def _call(self, article: NewsArticle) -> dict:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            # Stable, cacheable prefix: the system prompt never varies between calls.
            "system": [
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": article_prompt(article, self.max_text_chars)}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
        }
        if self.use_fallbacks:
            # Server-side re-run on Anthropic's recommended model if a request is declined.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        response = await self.client.beta.messages.create(**kwargs)
        if response.stop_reason == "refusal":
            raise LlmClassificationError(f"classification declined for {article.source_id}")
        if response.stop_reason == "max_tokens":
            raise LlmClassificationError(f"output truncated for {article.source_id}")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise LlmClassificationError(f"no text block in response for {article.source_id}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LlmClassificationError(f"invalid JSON for {article.source_id}") from exc
