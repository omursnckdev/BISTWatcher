"""Telegram bot: commands, messages and alerts (no network: a mock transport)."""

import importlib.util
import json
import time
from dataclasses import replace
from datetime import date, datetime, timedelta

import httpx
import pytest

from bist_quant.data.market_data import ISTANBUL_TZ, LiveQuote
from bist_quant.gui import services
from bist_quant.gui import telegram_bot as tb
from bist_quant.gui.prefs import Prefs
from bist_quant.models.signals import SignalType
from bist_quant.strategy.exit_check import ExitAction, ExitCheck, Holding


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    prefs = Prefs(provider="synthetic", universe="BIST30")
    settings = services.load_app_settings(home, prefs)
    symbols = services.universe_symbols(settings, prefs)
    return settings, symbols, services.scan(settings, symbols, symbols)


def test_config_round_trip(tmp_path):
    assert tb.load_config(tmp_path) == tb.TelegramConfig()
    cfg = tb.TelegramConfig(enabled=True, token="1:abc", chat_ids=[42, -1001], notify_buys=False)
    tb.save_config(tmp_path, cfg)
    assert tb.load_config(tmp_path) == cfg
    assert cfg.ready
    (tmp_path / tb.CONFIG_FILE).write_text('{"chat_id": " 77 "}', encoding="utf-8")
    assert tb.load_config(tmp_path).chat_ids == [77]  # first-version file
    assert tb.parse_chat_ids("42, -100123 x 42") == [42, -100123]


def test_parse_command():
    assert tb.parse_command("/analiz@BistBot brsan") == ("analiz", ["BRSAN"])
    assert tb.parse_command("/Portföy") == ("portfoy", [])
    assert tb.parse_command("/yardım") == ("yardim", [])
    assert tb.parse_command("merhaba") is None
    assert tb.parse_command("/tara@BistBot", "bistbot") == ("tara", [])
    assert tb.parse_command("/tara@BaskaBot", "BistBot") is None


def test_split_long_text():
    text = "\n".join("x" * 100 for _ in range(100))
    parts = tb.split_text(text)
    assert len(parts) == 3 and all(len(p) <= 4000 for p in parts)


def test_messages_from_a_scan(demo):
    settings, symbols, result = demo
    text = tb.scan_text(result)
    assert "Tarama" in text and "Sentetik" in text
    s = result.signals[0]
    caption = tb.analysis_caption(result, s.symbol)
    assert s.symbol in caption and "Sinyal" in caption and len(caption) <= 1024
    assert "analiz edilemedi" in tb.analysis_caption(result, "YOKBOYLE")
    if importlib.util.find_spec("matplotlib"):  # the gui extra; CI tests without it
        assert tb.chart_png(result, s.symbol).startswith(b"\x89PNG")
    holding = Holding(s.symbol, s.date - timedelta(days=30), s.close * 0.95, 100)
    pr = services.check_portfolio(settings, [holding], symbols)
    assert s.symbol in tb.portfolio_text([holding], pr)
    assert "Portföy: 1 pozisyon" in tb.status_text(result, pr, [holding], None, 5, "synthetic")
    assert "kontrol edilmedi" in tb.portfolio_text([holding], None)


def _report(result, holding, action, **kw):
    check = ExitCheck(
        holding.symbol,
        action,
        ["trend_exit"] if action is ExitAction.SELL else [],
        last_close=100.0,
        stop=90.0,
        tp1=110.0,
        tp2=120.0,
        pnl_pct=1.0,
        pnl_try=100.0,
        **kw,
    )
    return services.PortfolioReport(result, [check])


def test_portfolio_alert_once_per_change(demo):
    _, _, result = demo
    sym = result.signals[0].symbol
    h = Holding(sym, date(2026, 1, 2), 99.0, 100)
    state = tb.AlertState()
    assert tb.portfolio_alerts(state, [h], _report(result, h, ExitAction.HOLD)) == []
    assert tb.portfolio_alerts(state, [h], _report(result, h, ExitAction.HOLD)) == []
    alerts = tb.portfolio_alerts(state, [h], _report(result, h, ExitAction.SELL))
    assert len(alerts) == 1 and "TUT → <b>SAT</b>" in alerts[0] and sym in alerts[0]
    assert tb.portfolio_alerts(state, [h], _report(result, h, ExitAction.SELL)) == []
    # first sight of an actionable decision is reported
    fresh = tb.AlertState()
    assert len(tb.portfolio_alerts(fresh, [h], _report(result, h, ExitAction.SELL))) == 1
    # a removed holding is forgotten
    tb.portfolio_alerts(state, [], services.PortfolioReport(result, []))
    assert state.holdings == {}


def test_level_alerts_once_per_day(demo):
    _, _, result = demo
    sym = result.signals[0].symbol
    h = Holding(sym, date(2026, 1, 2), 99.0, 100)
    when = datetime(2026, 10, 6, 11, 0, tzinfo=ISTANBUL_TZ)
    scan = replace(result, live_quotes={sym: LiveQuote(sym, 89.0, when)})
    rep = _report(scan, h, ExitAction.HOLD)
    state = tb.AlertState()
    alerts = tb.level_alerts(state, [h], rep, when.date())
    assert len(alerts) == 1 and "stop" in alerts[0]
    assert tb.level_alerts(state, [h], rep, when.date()) == []
    assert len(tb.level_alerts(state, [h], rep, when.date() + timedelta(days=1))) == 1


def test_buy_alerts_only_new_symbols(demo):
    _, _, result = demo
    s0, s1 = result.signals[0], result.signals[1]
    buy = SignalType.BUY_CANDIDATE
    watch = s1.model_copy(update={"signal": SignalType.WATCH})
    day1 = replace(result, signals=[s0.model_copy(update={"signal": buy}), watch])
    state = tb.AlertState()
    first = tb.buy_alerts(state, day1)
    assert len(first) == 1 and s0.symbol in first[0] and s1.symbol not in first[0]
    assert tb.buy_alerts(state, day1) == []  # same session again: nothing new
    day2 = replace(
        day1,
        as_of=result.as_of + timedelta(days=1),
        signals=[s0.model_copy(update={"signal": buy}), s1.model_copy(update={"signal": buy})],
    )
    second = tb.buy_alerts(state, day2)
    assert len(second) == 1 and s1.symbol in second[0] and f"<b>{s0.symbol}</b>" not in second[0]
    assert tb.buy_alerts(state, day1) == []  # an older (historical) scan never alerts


def test_state_round_trip(tmp_path):
    st = tb.AlertState(holdings={"BRSAN": {"karar": "SAT", "sinyal": "İZLE"}}, buys=["THYAO"])
    tb.save_state(tmp_path, st)
    assert tb.load_state(tmp_path) == st


class FakeTelegram:
    """Answers Bot API calls like Telegram and records what the bot sent."""

    def __init__(self, updates):
        self.updates = list(updates)
        self.sent: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "TestBot"}})
        if method == "getUpdates":
            batch, self.updates = self.updates, []
            if not batch:
                time.sleep(0.05)
            return httpx.Response(200, json={"ok": True, "result": batch})
        body = (
            json.loads(request.content)
            if request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        self.sent.append((method, body))
        return httpx.Response(200, json={"ok": True, "result": True})


def _update(uid, chat_id, text, kind="private"):
    return {
        "update_id": uid,
        "message": {
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": kind},
            "text": text,
        },
    }


def test_runner_answers_only_the_authorised_chat():
    fake = FakeTelegram(
        [
            _update(1, 42, "/durum"),
            _update(2, 99, "/portfoy"),
            _update(3, -500, "/tara@TestBot", "group"),
        ]
    )
    cfg = tb.TelegramConfig(enabled=True, token="1:x", chat_ids=[42, -500])
    api = tb.TelegramApi(cfg.token, httpx.Client(transport=httpx.MockTransport(fake)))
    got, statuses = [], []
    runner = tb.BotRunner(cfg, got.append, lambda t, ok: statuses.append((t, ok)), api)
    runner.start()
    deadline = time.time() + 5
    while len(got) < 3 and time.time() < deadline:
        time.sleep(0.02)
    runner.send("merhaba")  # an alert: every authorised chat
    while sum(m == "sendMessage" for m, _ in fake.sent) < 2 and time.time() < deadline:
        time.sleep(0.02)
    runner.stop()
    assert [(m.chat_id, m.authorized, m.group) for m in got] == [
        (42, True, False),
        (99, False, False),
        (-500, True, True),
    ]
    assert statuses[0] == ("Çalışıyor: @TestBot", True)
    texts = [(b["chat_id"], b["text"]) for m, b in fake.sent if m == "sendMessage"]
    assert texts == [(42, "merhaba"), (-500, "merhaba")]
    assert any(m == "setMyCommands" for m, _ in fake.sent)


def test_runner_reports_a_bad_token():
    def handler(request):
        return httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
        )

    cfg = tb.TelegramConfig(enabled=True, token="bad", chat_ids=[1])
    api = tb.TelegramApi(cfg.token, httpx.Client(transport=httpx.MockTransport(handler)))
    statuses = []
    runner = tb.BotRunner(cfg, lambda m: None, lambda t, ok: statuses.append((t, ok)), api)
    runner._poll_loop()
    assert statuses == [("Bot başlatılamadı: Token geçersiz", False)]
