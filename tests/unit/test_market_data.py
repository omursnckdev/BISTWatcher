from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pandas as pd
import pytest

from bist_quant.data.market_data import (
    CachingProvider,
    CsvProvider,
    DataProviderError,
    MarketDataProvider,
    SyntheticProvider,
    YahooChartProvider,
    fetch_many,
    merge_history,
    write_bars_csv,
)

from ..conftest import make_bars

# 2026-09-24 and 2026-09-25 at 10:00 Istanbul (07:00 UTC)
CHART = {
    "chart": {
        "result": [
            {
                "meta": {"exchangeTimezoneName": "Europe/Istanbul"},
                "timestamp": [1790233200, 1790319600],
                "indicators": {
                    "quote": [
                        {
                            "open": [10.0, 11.0],
                            "high": [11.5, 12.0],
                            "low": [9.5, 10.5],
                            "close": [11.0, None],
                            "volume": [1000, 2000],
                        }
                    ],
                    "adjclose": [{"adjclose": [10.5, None]}],
                },
            }
        ],
        "error": None,
    }
}


def test_yahoo_parse_chart():
    frame = YahooChartProvider.parse_chart(CHART, "THYAO")
    assert list(frame.index.strftime("%Y-%m-%d")) == ["2026-09-24", "2026-09-25"]
    assert frame["adjusted_close"].iloc[0] == 10.5
    assert pd.isna(frame["close"].iloc[1])  # null candles are removed by clean_bars later


def test_yahoo_errors():
    with pytest.raises(DataProviderError):
        YahooChartProvider.parse_chart({"chart": {"result": None, "error": {"code": "x"}}}, "X")
    assert YahooChartProvider.ticker("thyao") == "THYAO.IS"


def test_yahoo_http_flow_with_mock_transport():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429)
        if "NOPE" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json=CHART)

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = YahooChartProvider(client=client, max_retries=2)
        provider_sleep = asyncio.sleep

        async def no_sleep(_):
            await provider_sleep(0)

        import bist_quant.data.market_data as md

        md.asyncio.sleep, orig = no_sleep, md.asyncio.sleep
        try:
            frame = await provider.get_daily_bars("THYAO", date(2026, 9, 1), date(2026, 9, 30))
            frames, errors = await fetch_many(
                provider, ["NOPE"], date(2026, 9, 1), date(2026, 9, 30)
            )
        finally:
            md.asyncio.sleep = orig
            await client.aclose()
        return frame, frames, errors

    frame, frames, errors = asyncio.run(run())
    assert len(frame) == 2 and calls[0].endswith("THYAO.IS")
    assert not frames and "NOPE" in errors


def test_csv_provider_roundtrip(tmp_path):
    bars = make_bars(n=30)
    write_bars_csv(bars, tmp_path / "ABC.csv")
    provider = CsvProvider(tmp_path)
    assert isinstance(provider, MarketDataProvider)
    out = asyncio.run(provider.get_daily_bars("abc", bars.index[5].date(), bars.index[9].date()))
    assert len(out) == 5
    pd.testing.assert_series_equal(
        out["close"], bars["close"].iloc[5:10], check_freq=False, check_names=False
    )
    with pytest.raises(DataProviderError):
        asyncio.run(provider.get_daily_bars("MISSING", date(2023, 1, 1), date(2024, 1, 1)))


def test_synthetic_is_deterministic_and_stable():
    p = SyntheticProvider()
    a = asyncio.run(p.get_daily_bars("THYAO", date(2024, 1, 1), date(2024, 12, 31)))
    b = asyncio.run(p.get_daily_bars("THYAO", date(2024, 6, 1), date(2025, 6, 30)))
    common = a.index.intersection(b.index)
    assert len(common) > 100
    pd.testing.assert_frame_equal(a.loc[common], b.loc[common])
    assert (a["high"] >= a[["open", "close"]].max(axis=1)).all()


def test_caching_provider_stores_and_reuses(tmp_path):
    class Counting(SyntheticProvider):
        calls = 0

        async def get_daily_bars(self, symbol, start, end):
            Counting.calls += 1
            return await super().get_daily_bars(symbol, start, end)

    cache = CachingProvider(Counting(), tmp_path, ttl_hours=24, session_close_time="00:00")
    start, end = date(2024, 1, 1), date(2024, 12, 31)
    first = asyncio.run(cache.get_daily_bars("XYZ", start, end))
    second = asyncio.run(cache.get_daily_bars("XYZ", start, end))
    assert Counting.calls == 1
    pd.testing.assert_frame_equal(first, second, check_freq=False)
    # Historical requests still cache data through today (no truncation at `end`).
    cached = pd.read_csv(tmp_path / "XYZ.csv")
    assert cached["date"].iloc[-1] > "2025-01-01"
    cache.force_refresh = True
    asyncio.run(cache.get_daily_bars("XYZ", start, end))
    assert Counting.calls == 2


def test_merge_history_rescales_old_adjustments():
    old = make_bars(n=10)
    new = old.iloc[5:].copy()
    new["adjusted_close"] = new["close"] * 0.9  # a dividend was applied since caching
    merged = merge_history(new, old)
    assert len(merged) == 10
    factors = merged["adjusted_close"] / merged["close"]
    assert factors.round(9).nunique() == 1
