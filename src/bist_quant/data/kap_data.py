"""KAP (Kamuyu Aydınlatma Platformu) disclosure ingestion.

Uses the JSON endpoints behind kap.org.tr's public disclosure search. These are
not a documented public API: the payload was taken from the website and may
change. All raw responses are stored on disk (spec: store raw source data).

Storage layout (``data/raw/kap``):

* ``members.json``                   ticker -> KAP company id (``mkkMemberOid``)
* ``disclosures/YYYY-MM.json``       ``{"members": [...], "rows": [...]}`` per month
* ``details/<disclosureIndex>.txt``  extracted body text (fetched on demand)
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import httpx

from bist_quant.data.market_data import ISTANBUL_TZ
from bist_quant.logging import get_logger, log_event

log = get_logger(__name__)

BASE = "https://www.kap.org.tr"
ROW_CAP = 2000  # the endpoint silently truncates at this many rows
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept-Language": "tr",
    "Origin": BASE,
    "Referer": f"{BASE}/tr/bildirim-sorgu",
}


class KapError(RuntimeError):
    pass


class KapProvider(Protocol):
    """Vendor-agnostic disclosure source (spec §67)."""

    async def resolve_members(self, symbols: list[str]) -> dict[str, str]: ...

    async def disclosures(self, start: date, end: date, member_oids: list[str]) -> list[dict]: ...

    async def disclosure_text(self, disclosure_index: int) -> str: ...


def _query_body(start: date, end: date, member_oids: list[str]) -> dict:
    return {
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
        "memberType": "IGS",
        "mkkMemberOidList": member_oids,
        "inactiveMkkMemberOidList": [],
        "disclosureClass": "",
        "subjectList": [],
        "isLate": "",
        "mainSector": "",
        "sector": "",
        "subSector": "",
        "marketOid": "",
        "index": "",
        "bdkReview": "",
        "bdkMemberOidList": [],
        "year": "",
        "term": "",
        "ruleType": "",
        "period": "",
        "fromSrc": False,
        "srcCategory": "",
        "disclosureIndexList": [],
    }


class KapClient:
    """Async client for kap.org.tr with retry/backoff and row-cap splitting."""

    def __init__(
        self,
        timeout: float = 60.0,
        concurrency: int = 3,
        max_retries: int = 4,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout, headers=HEADERS)
        self._owns = client is None
        self._sem = asyncio.Semaphore(concurrency)
        self._retries = max_retries

    async def aclose(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        last = "unknown"
        async with self._sem:
            for attempt in range(self._retries + 1):
                try:
                    resp = await self._client.request(method, url, **kw)
                except httpx.HTTPError as exc:
                    last = f"{type(exc).__name__}: {exc}"
                else:
                    if resp.status_code == 200:
                        return resp
                    last = f"HTTP {resp.status_code}"
                    if resp.status_code not in (429, 500, 502, 503, 504):
                        break
                if attempt < self._retries:
                    await asyncio.sleep(2**attempt)
        log_event(log, "kap_request_failed", level=30, url=url, error=last)
        raise KapError(f"KAP request failed: {url} ({last})")

    async def resolve_members(self, symbols: list[str]) -> dict[str, str]:
        """Ticker -> KAP company id via the site search (a company may list several codes)."""

        async def one(sym: str) -> tuple[str, str | None]:
            resp = await self._request(
                "POST", f"{BASE}/tr/api/search/combined", json={"keyword": sym}
            )
            for category in resp.json():
                for item in category.get("results", []):
                    codes = [
                        c.strip().upper() for c in (item.get("cmpOrFundCode") or "").split(",")
                    ]
                    if item.get("searchType") == "C" and sym.upper() in codes:
                        return sym, item["memberOrFundOid"]
            return sym, None

        pairs = await asyncio.gather(*(one(s) for s in symbols))
        found = {s: oid for s, oid in pairs if oid}
        missing = sorted(set(symbols) - set(found))
        if missing:
            log_event(log, "kap_members_unresolved", level=30, symbols=missing)
        return found

    async def disclosures(self, start: date, end: date, member_oids: list[str]) -> list[dict]:
        """All disclosures of ``member_oids`` in [start, end]; splits windows at the row cap."""
        resp = await self._request(
            "POST",
            f"{BASE}/tr/api/disclosure/members/byCriteria",
            json=_query_body(start, end, member_oids),
        )
        rows = resp.json()
        if not isinstance(rows, list):
            raise KapError(f"unexpected KAP response: {str(rows)[:200]}")
        if len(rows) >= ROW_CAP:
            if start >= end:
                if len(member_oids) > 1:
                    half = len(member_oids) // 2
                    a, b = await asyncio.gather(
                        self.disclosures(start, end, member_oids[:half]),
                        self.disclosures(start, end, member_oids[half:]),
                    )
                    return a + b
                log_event(log, "kap_row_cap_single_day", level=30, day=str(start))
                return rows
            mid = start + (end - start) // 2
            a, b = await asyncio.gather(
                self.disclosures(start, mid, member_oids),
                self.disclosures(mid + timedelta(days=1), end, member_oids),
            )
            return a + b
        return rows

    async def disclosure_text(self, disclosure_index: int) -> str:
        resp = await self._request("GET", f"{BASE}/tr/Bildirim/{disclosure_index}")
        return extract_disclosure_text(resp.text)


_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_EN_BLOCK = re.compile(r"<(div|td|span)[^>]*content-en[^>]*>.*?</\1>", re.S | re.I)


def extract_disclosure_text(page: str, max_chars: int = 20_000) -> str:
    """Best-effort plain text of a KAP disclosure page (Turkish content only)."""
    body = _SCRIPT.sub(" ", page)
    body = _EN_BLOCK.sub(" ", body)
    text = html_lib.unescape(_TAG.sub(" ", body))
    text = re.sub(r"\s+", " ", text).strip()
    # Drop the site chrome before the disclosure header when recognisable.
    for marker in ("Bildirim İçeriklerinde ve Eklerinde Ara", "Özet Bilgi"):
        pos = text.find(marker)
        if 0 <= pos < len(text) // 2:
            text = text[pos + len(marker) :].strip()
    return text[:max_chars]


def parse_publish_date(value: str) -> datetime:
    """KAP ``publishDate`` ('25.09.2026 22:44:39') -> aware Europe/Istanbul datetime."""
    return datetime.strptime(value.strip(), "%d.%m.%Y %H:%M:%S").replace(tzinfo=ISTANBUL_TZ)


# --------------------------------------------------------------------------- storage


class KapStore:
    """On-disk cache of member ids, monthly disclosure lists and detail texts."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        (self.root / "disclosures").mkdir(parents=True, exist_ok=True)
        (self.root / "details").mkdir(parents=True, exist_ok=True)

    # members
    def members(self) -> dict[str, str]:
        path = self.root / "members.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save_members(self, members: dict[str, str]) -> None:
        merged = {**self.members(), **members}
        (self.root / "members.json").write_text(
            json.dumps(merged, indent=1, sort_keys=True), encoding="utf-8"
        )

    # monthly disclosure files
    def _month_path(self, month: date) -> Path:
        return self.root / "disclosures" / f"{month:%Y-%m}.json"

    def month(self, month: date) -> dict:
        path = self._month_path(month)
        if not path.exists():
            return {"members": [], "rows": [], "fetched_at": None}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_month(self, month: date, members: list[str], rows: list[dict]) -> None:
        payload = {
            "members": sorted(set(members)),
            "rows": rows,
            "fetched_at": datetime.now(ISTANBUL_TZ).isoformat(),
        }
        self._month_path(month).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def rows(self, start: date, end: date) -> list[dict]:
        out: list[dict] = []
        for month in month_starts(start, end):
            out.extend(self.month(month)["rows"])
        return out

    # details
    def detail(self, disclosure_index: int) -> str | None:
        path = self.root / "details" / f"{disclosure_index}.txt"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def save_detail(self, disclosure_index: int, text: str) -> None:
        (self.root / "details" / f"{disclosure_index}.txt").write_text(text, encoding="utf-8")


def month_starts(start: date, end: date) -> list[date]:
    months, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        months.append(cur)
        cur = date(cur.year + (cur.month // 12), cur.month % 12 + 1, 1)
    return months


def _month_end(month: date) -> date:
    nxt = date(month.year + (month.month // 12), month.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


async def sync_disclosures(
    client: KapProvider,
    store: KapStore,
    symbols: list[str],
    start: date,
    end: date,
    refresh_recent_days: int = 3,
) -> dict[str, int]:
    """Download missing disclosure months for ``symbols`` (incremental, resumable).

    A month is complete once it ended more than ``refresh_recent_days`` ago and
    covers every requested company; otherwise only the missing part is fetched.
    """
    members = store.members()
    unknown = [s for s in symbols if s not in members]
    if unknown:
        members.update(await client.resolve_members(unknown))
        store.save_members(members)
    wanted = {members[s] for s in symbols if s in members}
    today = datetime.now(ISTANBUL_TZ).date()
    stats = {"months_fetched": 0, "rows_added": 0}
    for month in month_starts(start, end):
        cached = store.month(month)
        covered = set(cached["members"])
        month_end = _month_end(month)
        recent = (today - month_end).days <= refresh_recent_days
        missing = sorted(wanted - covered)
        refetch = sorted(wanted) if recent else missing
        if not refetch:
            continue
        rows = await client.disclosures(month, min(month_end, today), refetch)
        keep = [r for r in cached["rows"]] if not recent else []
        seen = {r.get("disclosureIndex") for r in keep}
        new_rows = [r for r in rows if r.get("disclosureIndex") not in seen]
        store.save_month(
            month, sorted(covered | wanted) if not recent else sorted(wanted), keep + new_rows
        )
        stats["months_fetched"] += 1
        stats["rows_added"] += len(new_rows)
        log_event(log, "kap_month_synced", month=f"{month:%Y-%m}", rows=len(new_rows))
    return stats
