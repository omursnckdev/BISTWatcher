from __future__ import annotations

import asyncio
import json
import os
from datetime import date

import pytest

from bist_quant.data.market_data import SyntheticProvider, YahooChartProvider
from bist_quant.data.universe import load_universe
from bist_quant.main import main
from bist_quant.models.signals import SignalType
from bist_quant.scanner import ScanError, run_scan

AS_OF = date(2025, 3, 31)


@pytest.fixture(scope="module")
def scan(settings):
    symbols = load_universe(settings.universe, "BIST30")
    return asyncio.run(run_scan(settings, symbols, SyntheticProvider(), as_of=AS_OF))


def test_scan_produces_ranked_explained_signals(scan, settings):
    assert scan.as_of == AS_OF
    assert len(scan.signals) == 30
    scores = [s.score for s in scan.signals]
    assert scores == sorted(scores, reverse=True)
    for s in scan.signals:
        assert 0 <= s.score <= 100
        assert s.explanation.positive_factors or s.explanation.negative_factors
        assert set(s.components) >= {"trend", "momentum", "volume", "relative_strength"}
        assert s.buy_threshold == settings.strategy.buy_threshold[scan.regime.regime]
    assert scan.regime.breadth_pct is not None


def test_buy_signals_carry_full_risk_plan(settings):
    # Loosen thresholds so the synthetic data produces BUY candidates to inspect.
    s = settings.model_copy(deep=True)
    s.strategy.buy_threshold = dict.fromkeys(s.strategy.buy_threshold, 55.0)
    s.strategy.weak_setup_threshold = 50
    s.liquidity.min_avg_turnover_try = 0
    symbols = load_universe(s.universe, "BIST50")
    result = asyncio.run(run_scan(s, symbols, SyntheticProvider(), as_of=AS_OF))
    buys = [
        x
        for x in result.signals
        if x.signal in (SignalType.BUY_CANDIDATE, SignalType.STRONG_BUY_CANDIDATE)
    ]
    assert buys
    for b in buys:
        p = b.risk
        assert p is not None and p.stop < p.entry < p.tp1 < p.tp2
        assert p.risk_reward >= s.risk.minimum_rr
        assert p.shares > 0 and p.capital_at_risk <= s.risk.portfolio_equity * 0.01 + 1e-6
        assert b.liquidity_ok and b.data_ok


def test_missing_benchmark_raises(settings):
    class NoIndex(SyntheticProvider):
        async def get_daily_bars(self, symbol, start, end):
            if symbol == "XU100":
                raise ValueError("down")
            return await super().get_daily_bars(symbol, start, end)

    with pytest.raises(ScanError):
        asyncio.run(run_scan(settings, ["THYAO"], NoIndex(), as_of=AS_OF))


def test_short_history_symbol_is_skipped(settings):
    class Young(SyntheticProvider):
        async def get_daily_bars(self, symbol, start, end):
            frame = await super().get_daily_bars(symbol, start, end)
            return frame.iloc[-50:] if symbol == "NEWCO" else frame

    result = asyncio.run(run_scan(settings, ["THYAO", "NEWCO"], Young(), as_of=AS_OF))
    assert "NEWCO" in result.skipped
    assert [s.symbol for s in result.signals] == ["THYAO"]


def test_cli_scan_table(capsys):
    code = main(["scan", "--provider", "synthetic", "--as-of", "2025-03-31", "--top", "5"])
    out = capsys.readouterr().out
    assert code == 0
    assert "BIST QUANT SCANNER" in out and "SYNTHETIC DATA" in out
    assert "SYMBOL" in out and "SCORE" in out and "SIGNAL" in out


def test_cli_json_and_analyze(capsys):
    assert (
        main(
            [
                "scan",
                "--provider",
                "synthetic",
                "--as-of",
                "2025-03-31",
                "--symbols",
                "THYAO",
                "ASELS",
                "--json",
                "-",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert {s["symbol"] for s in payload["signals"]} == {"THYAO", "ASELS"}
    assert payload["signals"][0]["components"]["news"] is None

    assert main(["analyze", "THYAO", "--provider", "synthetic", "--as-of", "2025-03-31"]) == 0
    out = capsys.readouterr().out
    assert "TOTAL SCORE" in out and "Reasons:" in out and "Stop Loss" in out


def test_cli_universe(capsys):
    assert main(["universe", "--universe", "BIST30"]) == 0
    assert len(capsys.readouterr().out.split()) == 30


@pytest.mark.skipif(
    os.environ.get("BIST_QUANT_NETWORK_TESTS") != "1",
    reason="set BIST_QUANT_NETWORK_TESTS=1 to hit the live Yahoo Finance API",
)
def test_live_yahoo_download():
    async def run():
        async with YahooChartProvider() as p:
            return await p.get_daily_bars("XU100", date(2024, 1, 1), date(2024, 12, 31))

    frame = asyncio.run(run())
    assert len(frame) > 200


DEMO = ["--as-of", "2025-03-31", "--json", "-"]


def test_cli_turkish_names_match_english(capsys):
    en = ["scan", "--provider", "synthetic", "--symbols", "THYAO", "ASELS", *DEMO]
    tr = ["tara", "--kaynak", "synthetic", "--semboller", "THYAO", "ASELS", "--tarih",
          "2025-03-31", "--json", "-"]  # fmt: skip
    assert main(en) == 0
    english = capsys.readouterr().out
    assert main(tr) == 0
    assert capsys.readouterr().out == english

    assert main(["evren", "--evren", "BIST30"]) == 0
    assert len(capsys.readouterr().out.split()) == 30


def test_cli_buy_threshold_shifts_every_regime(capsys):
    base = ["tara", "--kaynak", "synthetic", "--semboller", "THYAO", *DEMO]
    assert main(base) == 0
    default = json.loads(capsys.readouterr().out)["signals"][0]
    assert main([*base, "--alim-esigi", "55"]) == 0
    shifted = json.loads(capsys.readouterr().out)["signals"][0]
    # The default BULL threshold is 60, so every regime moves down by 5.
    assert shifted["buy_threshold"] == default["buy_threshold"] - 5
