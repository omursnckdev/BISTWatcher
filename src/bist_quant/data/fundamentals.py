"""Market capitalisation and FX history (used for the news scale adjustment, spec §23).

Market cap comes from İş Yatırım's public stock-data endpoint (daily ``PD`` field, TRY);
EUR/TRY from Yahoo Finance; USD/TRY is included in the İş Yatırım response. Everything
is cached on disk. Unofficial sources: failures degrade to "no scale adjustment".
"""

from __future__ import annotations

import asyncio
import time
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from bist_quant.logging import get_logger, log_event

log = get_logger(__name__)

ISY_URL = "https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"


class MarketCapStore:
    def __init__(self, root: Path, ttl_hours: float = 24.0, concurrency: int = 3) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_hours * 3600
        self._sem = asyncio.Semaphore(concurrency)

    def _path(self, symbol: str) -> Path:
        return self.root / f"{symbol.upper()}.csv"

    def load(self, symbol: str) -> pd.DataFrame | None:
        path = self._path(symbol)
        if not path.exists():
            return None
        return pd.read_csv(path, parse_dates=["date"]).set_index("date")

    async def fetch(
        self, client: httpx.AsyncClient, symbol: str, start: date, end: date
    ) -> pd.DataFrame | None:
        path = self._path(symbol)
        if path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            cached = self.load(symbol)
            if cached is not None and len(cached) and cached.index[0] <= pd.Timestamp(start):
                return cached
        params = {
            "hisse": symbol.upper(),
            "startdate": start.strftime("%d-%m-%Y"),
            "enddate": end.strftime("%d-%m-%Y"),
        }
        async with self._sem:
            try:
                resp = await client.get(ISY_URL, params=params)
                rows = resp.json().get("value") or []
            except (httpx.HTTPError, ValueError) as exc:
                log_event(log, "market_cap_fetch_failed", level=30, symbol=symbol, error=str(exc))
                return self.load(symbol)
        if not rows:
            return self.load(symbol)
        frame = (
            pd.DataFrame(
                {
                    "date": pd.to_datetime([r["HGDG_TARIH"] for r in rows], format="%d-%m-%Y"),
                    "market_cap_try": [r.get("PD") for r in rows],
                    "usdtry": [r.get("DD_DEGER") for r in rows],
                }
            )
            .set_index("date")
            .sort_index()
        )
        frame.to_csv(path)
        return frame

    async def fetch_many(
        self, symbols: list[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        async with httpx.AsyncClient(timeout=90, headers={"User-Agent": "Mozilla/5.0"}) as c:
            frames = await asyncio.gather(*(self.fetch(c, s, start, end) for s in symbols))
        return {s: f for s, f in zip(symbols, frames, strict=True) if f is not None}


def value_on(series: pd.Series, when: pd.Timestamp) -> float | None:
    """Last known value at or before ``when`` (point-in-time)."""
    past = series.loc[:when].dropna()
    return float(past.iloc[-1]) if len(past) else None
