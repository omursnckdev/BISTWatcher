"""Qt-free parts of the desktop app: preferences, translations, services, reports."""

from datetime import date, timedelta

import pytest

from bist_quant.gui import paths, report, services
from bist_quant.gui.prefs import (
    Prefs,
    load_holdings,
    load_prefs,
    save_holdings,
    save_prefs,
)
from bist_quant.gui.texts import money, tr
from bist_quant.models.signals import MarketRegime
from bist_quant.strategy.exit_check import Holding


def test_prefs_round_trip_and_defaults(tmp_path):
    assert load_prefs(tmp_path) == Prefs()
    (tmp_path / "tercihler.json").write_text("{broken", encoding="utf-8")
    assert load_prefs(tmp_path) == Prefs()
    p = Prefs(provider="synthetic", universe="CUSTOM", custom_symbols=["THYAO"], buy_threshold=60)
    save_prefs(tmp_path, p)
    assert load_prefs(tmp_path) == p


def test_prefs_overrides_shift_all_regimes(tmp_path):
    s = services.load_app_settings(
        tmp_path, Prefs(provider="synthetic", buy_threshold=60, minimum_rr=1.5)
    )
    bull = s.strategy.buy_threshold[MarketRegime.BULL] + s.strategy.buy_threshold_offset
    assert bull == 60
    assert s.risk.minimum_rr == 1.5
    assert s.project_root == tmp_path
    assert not s.news.enabled  # never for synthetic data


def test_holdings_round_trip_skips_bad_rows(tmp_path):
    h = [Holding("THYAO", date(2026, 9, 1), 300.5, 100, stop=280.0, tp1_taken=True)]
    save_holdings(tmp_path, h)
    assert load_holdings(tmp_path) == h
    (tmp_path / "portfoy.json").write_text('[{"symbol": "X"}]', encoding="utf-8")
    assert load_holdings(tmp_path) == []


def test_translation():
    assert tr("Price above EMA50") == "Fiyat EMA50 üzerinde"
    assert tr("RSI healthy at 61") == "RSI sağlıklı: 61"
    assert tr("Score below the BULL regime buy threshold (65)") == (
        "Puan BOĞA piyasa durumunun AL eşiğinin (65) altında"
    )
    assert tr("EMA20 above EMA50 (insufficient data)") == "EMA20, EMA50 üzerinde (yetersiz veri)"
    assert tr("something new") == "something new"
    assert money(1234567.891) == "1.234.567,89"
    assert money(None) == "-"


def test_user_config_overrides_bundled(tmp_path):
    assert paths.config_dir(tmp_path) == paths.bundled_config_dir()
    target = paths.export_default_config(tmp_path)
    text = (target / "settings.yaml").read_text(encoding="utf-8")
    (target / "settings.yaml").write_text(
        text.replace("min_history_bars: 250", "min_history_bars: 260"), encoding="utf-8"
    )
    s = services.load_app_settings(tmp_path, Prefs(provider="synthetic"))
    assert s.data.min_history_bars == 260


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    prefs = Prefs(provider="synthetic", universe="BIST30")
    settings = services.load_app_settings(home, prefs)
    symbols = services.universe_symbols(settings, prefs)
    return home, settings, symbols, services.scan(settings, symbols, symbols)


def test_scan_rows_and_exports(demo):
    pytest.importorskip("openpyxl")
    home, _, symbols, result = demo
    rows = report.signal_rows(result)
    assert len(rows) == len(result.signals) == len(symbols)
    assert {"Sembol", "Puan", "Sinyal", "Elindeyse", "Stop", "TP1", "Lot"} <= set(rows[0])
    assert {r["Elindeyse"] for r in rows} <= {"TUT", "SAT", "-"}
    for name, fn in (
        ("r.xlsx", report.export_scan_excel),
        ("r.html", report.export_scan_html),
        ("r.csv", report.export_scan_csv),
    ):
        path = fn(result, home / name)
        assert path.stat().st_size > 1000
    assert "Sentetik demo verisi" in " ".join(report.summary_lines(result))


def test_portfolio_check(demo):
    _, settings, symbols, result = demo
    top = result.signals[0]
    holdings = [
        Holding(top.symbol, top.date - timedelta(days=20), top.close, 10),
        Holding(result.signals[-1].symbol, top.date - timedelta(days=5), 1.0, 10),
    ]
    pr = services.check_portfolio(settings, holdings, symbols)
    assert len(pr.checks) == 2
    rows = report.portfolio_rows(holdings, pr.checks, pr.scan)
    assert rows[0]["Karar"] in {"TUT", "SAT", "KISMİ SAT"}
    assert rows[0]["Güncel Sinyal"] != "-"


def test_backtest_summary(demo):
    pytest.importorskip("openpyxl")
    home, settings, symbols, _ = demo
    bt = services.backtest(settings, symbols[:6], "BIST30", date(2024, 1, 2), date(2025, 3, 31))
    assert bt.symbols == 6
    assert "XU100 al-tut" in bt.benchmarks
    assert len(bt.equity) > 200
    path = report.export_backtest_excel(bt, home / "bt.xlsx")
    assert path.exists()


def test_live_quotes_are_shown_next_to_the_close(demo):
    from datetime import datetime

    from bist_quant.data.market_data import ISTANBUL_TZ, LiveQuote

    _, _, _, result = demo
    top = result.signals[0]
    when = datetime.combine(result.as_of + timedelta(days=1), datetime.min.time(), ISTANBUL_TZ)
    result.live_quotes = {top.symbol: LiveQuote(top.symbol, top.close * 0.99, when)}
    try:
        rows = report.signal_rows(result)
        assert rows[0]["Son Kapanış"] == top.close
        assert rows[0]["Anlık %"] == -1.0
        assert rows[1]["Anlık"] is None
        assert "Anlık" in report.price_basis(result)
    finally:
        result.live_quotes = {}
    assert "Anlık" not in report.signal_rows(result)[0]


def test_auto_refresh_decisions():
    from datetime import datetime

    from bist_quant.data.market_data import ISTANBUL_TZ

    def at(day, hh, mm=0):
        return datetime(2026, 10, day, hh, mm, tzinfo=ISTANBUL_TZ)  # 5 Oct 2026 = Monday

    mon, tue, fri = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 9)
    assert services.last_completed_session(at(6, 10, 13)) == mon
    assert services.last_completed_session(at(6, 18, 20)) == tue
    assert services.last_completed_session(at(11, 12)) == fri  # Sunday
    assert services.last_completed_session(at(12, 9)) == fri  # Monday before the open

    act = services.auto_refresh_action
    assert act(at(6, 10, 13), None, "yahoo") == "scan"
    assert act(at(6, 10, 13), mon, "yahoo") == "quotes"  # session open: prices only
    assert act(at(6, 18, 20), mon, "yahoo") == "scan"  # Tuesday's bar is final now
    assert act(at(6, 18, 20), tue, "yahoo") == "none"
    assert act(at(11, 12), fri, "yahoo") == "none"  # weekend
    assert act(at(6, 10, 13), mon, "synthetic") == "none"


def test_backtest_uses_portfolio_size_and_traded_prices(demo):
    home, _, symbols, _ = demo
    settings = services.load_app_settings(
        home, Prefs(provider="synthetic", portfolio_equity=100_000)
    )
    bt = services.backtest(settings, symbols[:6], "BIST30", date(2024, 1, 2), date(2025, 3, 31))
    assert bt.initial_equity == 100_000
    assert bt.equity.iloc[0] == pytest.approx(100_000, rel=0.01)
    assert bt.requested_start == date(2024, 1, 2)
    t = bt.trades
    assert len(t)
    # min_position_pct (2%) drops token positions; nothing exceeds max_position_pct (20%)
    assert t["equity_pct"].min() >= 2.0 - 1e-6
    assert t["equity_pct"].max() <= 20.0 + 0.5
    assert (t["position_value"] > 0).all()


def test_traded_scale_converts_adjusted_prices():
    import pandas as pd

    idx = pd.bdate_range("2024-01-01", periods=3)
    bars = {
        "AAA": pd.DataFrame({"close": [50.0, 51, 52], "raw_close": [100.0, 102, 104]}, index=idx)
    }
    trades = pd.DataFrame(
        {
            "symbol": ["AAA"],
            "entry_date": [idx[1]],
            "entry_price": [51.0],
            "exit_price": [52.0],
            "shares": [200],
        }
    )
    out = services.traded_scale(trades, bars, pd.Series([1e5, 1e5, 1e5], index=idx))
    assert out.loc[0, "entry_price"] == 102 and out.loc[0, "exit_price"] == 104
    assert out.loc[0, "shares"] == 100
    assert out.loc[0, "position_value"] == 51 * 200
    assert out.loc[0, "equity_pct"] == pytest.approx(10.2)
