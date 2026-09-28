"""Turn raw KAP rows into :class:`NewsArticle` objects and extract amounts from text."""

from __future__ import annotations

import re

from bist_quant.data.kap_data import BASE, parse_publish_date
from bist_quant.models.news import NewsArticle

# Market-wide notices (circuit breakers, trading-system announcements) are not company news.
IGNORED_TITLES = ("BORSA İSTANBUL BISTECH DEVRE KESİCİ",)


def _codes(value: str | None) -> list[str]:
    return [c.strip().upper() for c in (value or "").split(",") if c.strip()]


def kap_row_to_articles(row: dict, universe: set[str]) -> list[NewsArticle]:
    """One article per KAP row, tagged with the universe tickers it refers to.

    ``stockCodes`` lists the issuer's own codes; ``relatedStocks`` is used for
    notices published by KAP / MKK / SPK about specific companies.
    """
    title = row.get("kapTitle") or ""
    if any(title.startswith(t) for t in IGNORED_TITLES):
        return []
    symbols = [c for c in _codes(row.get("stockCodes")) if c in universe]
    if not symbols:
        symbols = [c for c in _codes(row.get("relatedStocks")) if c in universe]
    if not symbols or not row.get("publishDate"):
        return []
    index = row.get("disclosureIndex")
    return [
        NewsArticle(
            source="kap",
            source_id=str(index),
            symbols=sorted(set(symbols)),
            published_at=parse_publish_date(row["publishDate"]),
            company=title,
            subject=(row.get("subject") or "").strip(),
            summary=(row.get("summary") or "").strip(),
            url=f"{BASE}/tr/Bildirim/{index}" if index else None,
            raw=row,
        )
    ]


_NUMBER = r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)"
_SCALE = r"(milyar|milyon|bin)?"
_CURRENCY = r"(TL|TRY|Türk\s+Lirası|USD|ABD\s+Doları|Amerikan\s+Doları|Dolar|EUR|Avro|Euro)"
_AMOUNT = re.compile(rf"{_NUMBER}\s*{_SCALE}\s*{_CURRENCY}", re.I)
_SCALES = {"milyar": 1e9, "milyon": 1e6, "bin": 1e3}


def _currency_code(text: str) -> str:
    t = text.lower()
    if t in {"tl", "try"} or "lira" in t:
        return "TRY"
    if t in {"eur", "avro", "euro"}:
        return "EUR"
    return "USD"


def extract_amount(text: str | None) -> tuple[float, str] | None:
    """Largest monetary amount mentioned (Turkish number format), e.g.
    '225.000.000 Avro' -> (225e6, 'EUR'); '1,2 milyar TL' -> (1.2e9, 'TRY')."""
    if not text:
        return None
    best: tuple[float, str] | None = None
    for num, scale, cur in _AMOUNT.findall(text):
        value = float(num.replace(".", "").replace(",", "."))
        value *= _SCALES.get((scale or "").lower(), 1.0)
        code = _currency_code(cur)
        if best is None or value > best[0]:
            best = (value, code)
    return best
