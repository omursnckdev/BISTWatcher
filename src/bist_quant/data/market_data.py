"""Market data provider interface and adapters.

Strategy code never talks to a vendor directly: it depends on the
:class:`MarketDataProvider` protocol. Adapters shipped in Phase 1:

* :class:`YahooChartProvider` - keyless Yahoo Finance chart API (``<TICKER>.IS``).
* :class:`CsvProvider`        - local CSV files (bring-your-own vendor export).
* :class:`SyntheticProvider`  - deterministic random-walk data for offline demos/tests.
* :class:`CachingProvider`    - wraps any provider, stores raw data on disk.

Every provider returns a DataFrame indexed by a tz-naive ``DatetimeIndex`` named
``date`` with columns ``open, high, low, close, volume, adjusted_close``. Prices are
returned raw (as traded); adjustment happens in :mod:`bist_quant.data.quality`.
"""

from __future__ import annotations

import asyncio
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

from bist_quant.logging import get_logger, log_event
from bist_quant.models.market import OHLCV_COLUMNS

if TYPE_CHECKING:
    from bist_quant.config import Settings

log = get_logger(__name__)

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
INDEX_SYMBOLS = {"XU100", "XU030", "XU050", "XBANK", "XUSIN", "XUHIZ", "XUMAL", "XUTEK"}


class DataProviderError(RuntimeError):
    """Raised when a provider cannot return data for a symbol."""


@runtime_checkable
class MarketDataProvider(Protocol):
    """Vendor-agnostic daily-bar source."""

    name: str

    async def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return daily bars for ``symbol`` with ``start <= date <= end``."""
        ...


def empty_frame() -> pd.DataFrame:
    frame = pd.DataFrame(columns=OHLCV_COLUMNS, dtype="float64")
    frame.index = pd.DatetimeIndex([], name="date")
    return frame


def _slice(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    return frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]


# --------------------------------------------------------------------------- Yahoo


class YahooChartProvider:
    """Yahoo Finance v8 chart API. BIST tickers are mapped to ``<SYMBOL>.IS``.

    ``adjusted_close`` is Yahoo's split- and dividend-adjusted close. Yahoo's raw
    ``close`` is already split-adjusted; dividend adjustment is applied later.
    """

    name = "yahoo"
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        max_retries: int = 4,
        concurrency: int = 4,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries
        self._semaphore = asyncio.Semaphore(concurrency)

    @staticmethod
    def ticker(symbol: str) -> str:
        symbol = symbol.upper()
        return symbol if symbol.endswith(".IS") else f"{symbol}.IS"

    async def __aenter__(self) -> YahooChartProvider:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": "Mozilla/5.0 (bist-quant research scanner)"},
            )
        return self._client

    async def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        period1 = int(datetime.combine(start, datetime.min.time(), ISTANBUL_TZ).timestamp())
        period2 = int(
            datetime.combine(end + timedelta(days=1), datetime.min.time(), ISTANBUL_TZ).timestamp()
        )
        params = {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "div|split",
            "includeAdjustedClose": "true",
        }
        url = self.BASE_URL.format(ticker=self.ticker(symbol))
        payload = await self._request_json(url, params, symbol)
        frame = self.parse_chart(payload, symbol)
        log_event(log, "market_data_fetched", provider=self.name, symbol=symbol, bars=len(frame))
        return _slice(frame, start, end)

    async def _request_json(self, url: str, params: dict, symbol: str) -> dict:
        client = self._get_client()
        last_error: str = "unknown error"
        async with self._semaphore:
            for attempt in range(self._max_retries + 1):
                try:
                    resp = await client.get(url, params=params)
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                else:
                    if resp.status_code == 200:
                        return resp.json()
                    if resp.status_code == 404:
                        raise DataProviderError(f"{symbol}: unknown symbol (HTTP 404)")
                    last_error = f"HTTP {resp.status_code}"
                    if resp.status_code not in (429, 500, 502, 503, 504):
                        break
                if attempt < self._max_retries:
                    delay = 2**attempt
                    log_event(
                        log,
                        "market_data_retry",
                        provider=self.name,
                        symbol=symbol,
                        attempt=attempt + 1,
                        delay_s=delay,
                        error=last_error,
                    )
                    await asyncio.sleep(delay)
        raise DataProviderError(f"{symbol}: request failed ({last_error})")

    @staticmethod
    def parse_chart(payload: dict, symbol: str) -> pd.DataFrame:
        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise DataProviderError(f"{symbol}: {chart['error']}")
        results = chart.get("result") or []
        if not results:
            raise DataProviderError(f"{symbol}: empty chart response")
        result = results[0]
        timestamps = result.get("timestamp") or []
        if not timestamps:
            return empty_frame()
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        adj = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
        tz = ZoneInfo(result.get("meta", {}).get("exchangeTimezoneName") or "Europe/Istanbul")
        dates = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(tz).tz_localize(None)
        frame = pd.DataFrame(
            {
                "open": quote.get("open"),
                "high": quote.get("high"),
                "low": quote.get("low"),
                "close": quote.get("close"),
                "volume": quote.get("volume"),
                "adjusted_close": adj if adj is not None else quote.get("close"),
            },
            index=pd.DatetimeIndex(dates.normalize(), name="date"),
            dtype="float64",
        )
        # Yahoo occasionally emits the live bar twice (same date); keep the latest.
        return frame[~frame.index.duplicated(keep="last")].sort_index()


# --------------------------------------------------------------------------- CSV


class CsvProvider:
    """Reads ``<directory>/<SYMBOL>.csv`` with columns
    ``date,open,high,low,close,volume[,adjusted_close]``."""

    name = "csv"

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    async def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        path = self.directory / f"{symbol.upper()}.csv"
        if not path.exists():
            raise DataProviderError(f"{symbol}: CSV file not found at {path}")
        frame = read_bars_csv(path)
        return _slice(frame, start, end)


def read_bars_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame.columns = [c.strip().lower().replace(" ", "_") for c in frame.columns]
    if "adj_close" in frame.columns and "adjusted_close" not in frame.columns:
        frame = frame.rename(columns={"adj_close": "adjusted_close"})
    missing = {"date", "open", "high", "low", "close", "volume"} - set(frame.columns)
    if missing:
        raise DataProviderError(f"{path}: missing columns {sorted(missing)}")
    if "adjusted_close" not in frame.columns:
        frame["adjusted_close"] = frame["close"]
    frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.normalize()
    frame = frame.set_index("date")[OHLCV_COLUMNS].astype("float64").sort_index()
    frame.index.name = "date"
    return frame


def write_bars_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame[OHLCV_COLUMNS].copy()
    out.index = out.index.strftime("%Y-%m-%d")
    out.index.name = "date"
    out.to_csv(path)


# --------------------------------------------------------------------------- Synthetic


class SyntheticProvider:
    """Deterministic, offline data: one shared market factor + idiosyncratic noise.

    Output is **not** real market data. It exists so the full pipeline can be
    exercised without network access (demos, CI, tests).
    """

    name = "synthetic"

    def __init__(self, seed: int = 7) -> None:
        self.seed = seed

    def _streams(self, key: int, count: int) -> list[np.random.Generator]:
        # One independent stream per field: each draws from its own start, so the
        # first n values never depend on how long the requested series is.
        return [np.random.default_rng(s) for s in np.random.SeedSequence(key).spawn(count)]

    def _market_returns(self, n: int) -> np.ndarray:
        regimes, noise = self._streams(self.seed, 2)
        # Alternating drift regimes so bull / bear / neutral phases all appear.
        drift = np.repeat(regimes.choice([0.0012, 0.0004, -0.0008], size=n // 120 + 1), 120)[:n]
        return drift + noise.normal(0, 0.013, n)

    async def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        # Generate on a fixed calendar anchored in 2010 so any window is a slice of
        # one stable series (no look-ahead differences between runs).
        anchor = pd.Timestamp("2010-01-04")
        calendar = pd.bdate_range(anchor, pd.Timestamp(end), name="date")
        n = len(calendar)
        market = self._market_returns(n)
        sym = symbol.upper()
        rng, r_ret, r_open, r_span, r_vol = self._streams(zlib.crc32(sym.encode()) + self.seed, 5)
        if sym in INDEX_SYMBOLS:
            beta, idio, base, vol_base = 1.0, 0.002, 1000.0, 5e9
        else:
            beta = rng.uniform(0.7, 1.4)
            idio = rng.uniform(0.008, 0.02)
            base = rng.uniform(5, 150)
            vol_base = rng.uniform(2e6, 4e7)
        alpha = rng.normal(0.0002, 0.0006)
        rets = alpha + beta * market + r_ret.normal(0, idio, n)
        close = base * np.exp(np.cumsum(rets))
        prev = np.concatenate([[close[0]], close[:-1]])
        open_ = prev * np.exp(r_open.normal(0, idio / 3, n))
        span = np.abs(r_span.normal(0, 0.009, n)) * close
        high = np.maximum(open_, close) + span
        low = np.minimum(open_, close) - span
        volume = vol_base * np.exp(r_vol.normal(0, 0.35, n) + 8 * np.abs(rets))
        frame = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": np.round(volume),
                "adjusted_close": close,
            },
            index=calendar,
        )
        return _slice(frame, start, end)


# --------------------------------------------------------------------------- Caching


class CachingProvider:
    """Stores raw bars as ``<cache_dir>/<SYMBOL>.csv`` and reuses them within ``ttl_hours``.

    On a miss the inner provider is always queried up to *today* (even for historical
    ``end`` dates) and merged with what is already cached, so the cache never ends
    up truncated at a past date. Callers only ever receive ``start <= date <= end``.
    """

    def __init__(
        self,
        inner: MarketDataProvider,
        cache_dir: Path,
        ttl_hours: float,
        session_close_time: str = "18:15",
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_hours * 3600
        self.session_close = datetime.strptime(session_close_time, "%H:%M").time()
        self.force_refresh = False

    def _is_fresh(self, path: Path, now: datetime) -> bool:
        written = datetime.fromtimestamp(path.stat().st_mtime, ISTANBUL_TZ)
        if (now - written).total_seconds() > self.ttl_seconds:
            return False
        # A file written before today's close may hold a partial bar that is now final.
        close_today = datetime.combine(now.date(), self.session_close, ISTANBUL_TZ)
        return not (written < close_today <= now)

    def _path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol.upper()}.csv"

    async def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        path = self._path(symbol)
        now = datetime.now(ISTANBUL_TZ)
        cached = read_bars_csv(path) if path.exists() else None
        if cached is not None and len(cached) and not self.force_refresh:
            covers_start = cached.index[0] <= pd.Timestamp(start) + pd.Timedelta(days=7)
            if covers_start and self._is_fresh(path, now):
                log_event(log, "market_data_cache_hit", symbol=symbol)
                return _slice(cached, start, end)
        frame = await self.inner.get_daily_bars(symbol, start, max(end, now.date()))
        if len(frame):
            write_bars_csv(merge_history(frame, cached), path)
        return _slice(frame, start, end)


def merge_history(new: pd.DataFrame, old: pd.DataFrame | None) -> pd.DataFrame:
    """Merge freshly fetched bars over cached ones.

    New rows win. Older cached-only rows get their ``adjusted_close`` rescaled so the
    adjustment factor stays continuous if a dividend/split was applied since caching.
    """
    if old is None or old.empty:
        return new.sort_index()
    if new.empty:
        return old.sort_index()
    older = old.loc[old.index < new.index[0]].copy()
    overlap = new.index.intersection(old.index)
    if len(older) and len(overlap):
        d = overlap[0]
        new_factor = new.at[d, "adjusted_close"] / new.at[d, "close"]
        old_factor = old.at[d, "adjusted_close"] / old.at[d, "close"]
        if old_factor > 0 and np.isfinite(new_factor / old_factor):
            older["adjusted_close"] *= new_factor / old_factor
    return pd.concat([older, new]).sort_index()


async def fetch_many(
    provider: MarketDataProvider, symbols: list[str], start: date, end: date
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Fetch several symbols concurrently. Returns ``(frames, errors)``."""

    async def one(sym: str) -> tuple[str, pd.DataFrame | None, str | None]:
        try:
            return sym, await provider.get_daily_bars(sym, start, end), None
        except (DataProviderError, httpx.HTTPError, ValueError, KeyError) as exc:
            log_event(log, "market_data_error", level=30, symbol=sym, error=str(exc))
            return sym, None, str(exc)

    results = await asyncio.gather(*(one(s) for s in symbols))
    frames = {s: f for s, f, _ in results if f is not None}
    errors = {s: e for s, _, e in results if e is not None}
    return frames, errors


def build_provider(settings: Settings) -> MarketDataProvider:
    """Construct the configured provider (with raw-data caching for network sources)."""
    data = settings.data
    raw_dir = settings.resolve_path(data.raw_dir)
    if data.provider == "yahoo":
        inner = YahooChartProvider(
            timeout=data.request_timeout_seconds,
            max_retries=data.max_retries,
            concurrency=data.request_concurrency,
        )
        return CachingProvider(
            inner, raw_dir / "yahoo", data.cache_ttl_hours, data.session_close_time
        )
    if data.provider == "csv":
        return CsvProvider(raw_dir)
    return SyntheticProvider()
