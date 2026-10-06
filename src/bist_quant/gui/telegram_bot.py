"""Telegram bot for the desktop app (Qt-free).

The bot runs inside the open app and uses long polling, so it needs no inbound port
and works on a home connection. Only the configured chat gets answers; any other chat
that sends ``/start`` is told its chat id (so the owner can authorise it in the app)
and nothing else.

* :class:`TelegramApi` is a thin HTTP client for the Bot API.
* :class:`BotRunner` polls for messages and sends replies on background threads.
* The ``*_text`` functions turn the app's results into Turkish messages.
* :class:`AlertState` remembers what was already reported, so the same alert is not
  sent twice (portfolio decision changes, new AL signals, intraday stop/target hits).
"""

from __future__ import annotations

import contextlib
import html
import io
import json
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from pathlib import Path

import httpx

from bist_quant.gui.texts import (
    ACTION_TR,
    EXIT_REASON_TR,
    money,
    regime_text,
    signal_text,
    tr,
)
from bist_quant.models.signals import SignalType
from bist_quant.strategy.exit_check import ExitAction, ExitCheck, Holding

CONFIG_FILE = "telegram.json"
STATE_FILE = "telegram_durum.json"
API_URL = "https://api.telegram.org"
POLL_SECONDS = 25
STALE_SECONDS = 120  # messages older than this (sent while the app was closed) are ignored
BUY_SIGNALS = (SignalType.STRONG_BUY_CANDIDATE, SignalType.BUY_CANDIDATE)
PLAN_SIGNALS = (*BUY_SIGNALS, SignalType.WEAK_SETUP)

COMMANDS = [
    ("portfoy", "Portföyüm: SAT / TUT kararları ve K/Z"),
    ("tara", "Son tarama: AL sinyalleri ve en yüksek puanlar"),
    ("analiz", "Grafik ve analiz, örn. /analiz BRSAN"),
    ("durum", "Piyasa durumu ve uygulamanın durumu"),
    ("yenile", "Taramayı ve portföy kontrolünü şimdi yenile"),
    ("yardim", "Komut listesi"),
]
ALIASES = {
    "portföy": "portfoy",
    "portfolio": "portfoy",
    "p": "portfoy",
    "tarama": "tara",
    "scan": "tara",
    "a": "analiz",
    "grafik": "analiz",
    "yardım": "yardim",
    "help": "yardim",
    "status": "durum",
    "refresh": "yenile",
}


# ------------------------------------------------------------------ settings


@dataclass
class TelegramConfig:
    enabled: bool = False
    token: str = ""
    chat_id: int | None = None
    notify_portfolio: bool = True  # held position decision / signal changes
    notify_buys: bool = True  # new AL signals after a scan
    notify_levels: bool = True  # intraday price crossing a held position's stop / targets

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.token.strip())


def load_config(home: Path) -> TelegramConfig:
    try:
        raw = json.loads((home / CONFIG_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TelegramConfig()
    known = {f.name for f in fields(TelegramConfig)}
    try:
        cfg = TelegramConfig(**{k: v for k, v in raw.items() if k in known})
    except TypeError:
        return TelegramConfig()
    cfg.chat_id = parse_chat_id(cfg.chat_id)
    return cfg


def save_config(home: Path, cfg: TelegramConfig) -> None:
    (home / CONFIG_FILE).write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")


def parse_chat_id(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ Bot API


class TelegramError(Exception):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class TelegramApi:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self.token = token.strip()
        self.client = client or httpx.Client(timeout=httpx.Timeout(15, read=POLL_SECONDS + 15))

    def call(self, method: str, files: dict | None = None, **params):
        url = f"{API_URL}/bot{self.token}/{method}"
        try:
            if files:
                r = self.client.post(url, data=params, files=files)
            else:
                r = self.client.post(url, json=params)
        except httpx.HTTPError as exc:
            raise TelegramError(f"Telegram'a bağlanılamadı: {type(exc).__name__}") from None
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(f"Telegram yanıtı okunamadı (HTTP {r.status_code})") from None
        if not body.get("ok"):
            raise TelegramError(body.get("description", "bilinmeyen hata"), body.get("error_code"))
        return body.get("result")

    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: int | None, timeout: int = POLL_SECONDS) -> list[dict]:
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", **params)

    def send_message(self, chat_id: int, text: str) -> None:
        for part in split_text(text):
            self.call(
                "sendMessage",
                chat_id=chat_id,
                text=part,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

    def send_photo(self, chat_id: int, png: bytes, caption: str = "") -> None:
        self.call(
            "sendPhoto",
            files={"photo": ("grafik.png", png, "image/png")},
            chat_id=str(chat_id),
            caption=caption[:1024],
            parse_mode="HTML",
        )

    def set_commands(self) -> None:
        self.call(
            "setMyCommands",
            commands=[{"command": c, "description": d} for c, d in COMMANDS],
        )

    def close(self) -> None:
        self.client.close()


def split_text(text: str, limit: int = 4000) -> list[str]:
    """Telegram allows 4096 characters per message; split on line breaks."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line[:limit]
    if cur:
        parts.append(cur)
    return parts


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """``/analiz@MyBot brsan`` -> ("analiz", ["BRSAN"]); plain text -> None."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    head, *args = text[1:].split()
    name = head.split("@", 1)[0].lower()
    name = ALIASES.get(name, name)
    return name, [a.upper() for a in args]


# ------------------------------------------------------------------ runner


@dataclass
class Incoming:
    chat_id: int
    name: str
    text: str
    authorized: bool


class BotRunner:
    """Long-polls Telegram on one thread and sends queued messages on another.

    ``on_message`` and ``on_status`` are called from the polling thread; the UI must
    hand them over to its own thread (the Qt glue does this with signals).
    """

    def __init__(
        self,
        cfg: TelegramConfig,
        on_message: Callable[[Incoming], None],
        on_status: Callable[[str, bool], None],
        api: TelegramApi | None = None,
    ) -> None:
        self.cfg = cfg
        self.api = api or TelegramApi(cfg.token)
        self.on_message = on_message
        self.on_status = on_status
        self.stop_event = threading.Event()
        self.outbox: queue.Queue = queue.Queue()
        self.bot_name = ""
        self.threads: list[threading.Thread] = []

    # -- public, any thread
    def start(self) -> None:
        for target in (self._poll_loop, self._send_loop):
            t = threading.Thread(target=target, daemon=True, name=f"telegram-{target.__name__}")
            t.start()
            self.threads.append(t)

    def stop(self) -> None:
        self.stop_event.set()
        self.outbox.put(None)

    def send(self, text: str, chat_id: int | None = None) -> None:
        target = chat_id or self.cfg.chat_id
        if target:
            self.outbox.put(("text", target, text, None))

    def send_photo(self, png: bytes, caption: str, chat_id: int | None = None) -> None:
        target = chat_id or self.cfg.chat_id
        if target:
            self.outbox.put(("photo", target, caption, png))

    # -- threads
    def _poll_loop(self) -> None:
        try:
            me = self.api.get_me()
        except TelegramError as exc:
            msg = "Token geçersiz" if exc.code in (401, 404) else str(exc)
            self.on_status(f"Bot başlatılamadı: {msg}", False)
            return
        self.bot_name = me.get("username", "")
        with contextlib.suppress(TelegramError):  # the command menu is a convenience only
            self.api.set_commands()
        self.on_status(f"Çalışıyor: @{self.bot_name}", True)
        offset = None
        delay = 2.0
        while not self.stop_event.is_set():
            try:
                updates = self.api.get_updates(offset)
                delay = 2.0
            except TelegramError as exc:
                if exc.code == 409:
                    text = "Bu bot başka bir yerde de çalışıyor (uygulama iki kez mi açık?)"
                elif exc.code == 401:
                    self.on_status("Token geçersiz, bot durdu", False)
                    return
                else:
                    text = f"Bağlantı sorunu, yeniden deneniyor: {exc}"
                self.on_status(text, False)
                self.stop_event.wait(delay)
                delay = min(delay * 2, 60)
                continue
            if updates and offset is None:
                self.on_status(f"Çalışıyor: @{self.bot_name}", True)
            for u in updates:
                offset = u["update_id"] + 1
                self._dispatch(u)

    def _dispatch(self, update: dict) -> None:
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        text = msg.get("text") or ""
        if not chat or not text:
            return
        if time.time() - msg.get("date", 0) > STALE_SECONDS:
            return
        name = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
        incoming = Incoming(int(chat["id"]), name, text, chat["id"] == self.cfg.chat_id)
        try:
            self.on_message(incoming)
        except Exception as exc:  # noqa: BLE001 - a bad command must not stop polling
            if incoming.authorized:
                self.send(f"Hata: {html.escape(str(exc))}", incoming.chat_id)

    def _send_loop(self) -> None:
        while True:
            item = self.outbox.get()
            if item is None or self.stop_event.is_set():
                return
            kind, chat_id, text, png = item
            for attempt in range(3):
                try:
                    if kind == "photo":
                        self.api.send_photo(chat_id, png, text)
                    else:
                        self.api.send_message(chat_id, text)
                    break
                except TelegramError as exc:
                    if exc.code in (400, 401, 403) or attempt == 2:
                        self.on_status(f"Mesaj gönderilemedi: {exc}", False)
                        break
                    self.stop_event.wait(2 * (attempt + 1))


# ------------------------------------------------------------------ messages


def esc(x) -> str:
    return html.escape(str(x), quote=False)


def pct(x: float | None) -> str:
    return "-" if x is None else f"{'+' if x > 0 else ''}{money(x, 2)}%"


def welcome_text(chat_id: int, authorized: bool, configured: bool) -> str:
    if authorized:
        return "BISTWatcher botu bağlı.\n\n" + help_text()
    if configured:
        return (
            f"Bu bot özel. Sohbet kimliğiniz: <code>{chat_id}</code>. Bu sizseniz "
            "BISTWatcher'da Telegram sekmesinden bu kimliği yetkilendirin."
        )
    return (
        f"Merhaba! Sohbet kimliğiniz: <code>{chat_id}</code>\n"
        "BISTWatcher'da Telegram sekmesinde <b>Bu sohbeti kullan</b> düğmesine basın "
        "(ya da kimliği elle girip kaydedin)."
    )


def help_text() -> str:
    lines = ["<b>Komutlar</b>"] + [f"/{c} – {esc(d)}" for c, d in COMMANDS]
    lines += [
        "",
        "Bildirimler: portföyünüzdeki bir hissenin kararı değişince (örn. TUT → SAT), "
        "seans içinde fiyat stop ya da hedefe gelince ve tarama sonrası yeni AL sinyali "
        "çıkınca mesaj gelir. Bot, bilgisayarınızda BISTWatcher açıkken çalışır.",
    ]
    return "\n".join(lines)


def _signal_of(result, symbol: str):
    if result is None:
        return None
    return next((s for s in result.signals if s.symbol == symbol), None)


def _live(result, symbol: str):
    return (result.live_quotes or {}).get(symbol) if result is not None else None


def _synthetic_tag(result) -> str:
    return (
        "\n⚠️ <i>Sentetik demo verisi, gerçek fiyat değil.</i>"
        if (result is not None and result.provider == "synthetic")
        else ""
    )


def regime_line(result) -> str:
    r = result.regime
    thr = f" · AL eşiği {result.signals[0].buy_threshold:.0f}" if result.signals else ""
    return (
        f"Piyasa ({esc(r.index)}): <b>{regime_text(r.regime)}</b> "
        f"(puan {money(r.score.points, 1)}/{r.score.max_points:.0f}){thr}"
    )


def signal_line(result, s) -> str:
    q = _live(result, s.symbol)
    live = f" · anlık {money(q.price)} ({pct(100 * (q.price / s.close - 1))})" if q else ""
    line = (
        f"<b>{esc(s.symbol)}</b> {signal_text(s.signal)} · puan {money(s.score, 1)} · "
        f"{money(s.close)}{live}"
    )
    if s.signal in PLAN_SIGNALS and s.risk:
        p = s.risk
        line += (
            f"\n   giriş {money(p.entry_zone_low)}–{money(p.entry_zone_high)} · "
            f"stop {money(p.stop)} · TP1 {money(p.tp1)} · G/R {money(p.risk_reward, 1)}"
        )
    return line


def scan_text(result, top: int = 10) -> str:
    if result is None:
        return "Henüz tarama yapılmadı. /yenile ile başlatabilirsiniz."
    buys = [s for s in result.signals if s.signal in BUY_SIGNALS]
    others = [s for s in result.signals if s.signal not in BUY_SIGNALS][: max(top - len(buys), 3)]
    lines = [
        f"<b>Tarama · {result.as_of:%d.%m.%Y} kapanışı</b> ({len(result.signals)} hisse)",
        regime_line(result),
        "",
    ]
    if buys:
        lines.append(f"🟢 <b>AL sinyalleri ({len(buys)})</b>")
        lines += [signal_line(result, s) for s in buys]
    else:
        lines.append("AL sinyali yok.")
    if others:
        lines += ["", "<b>En yüksek puanlı diğerleri</b>"]
        lines += [signal_line(result, s) for s in others]
    lines.append("\nDetay ve grafik: /analiz SEMBOL")
    return "\n".join(lines) + _synthetic_tag(result)


def exit_reason(c: ExitCheck) -> str:
    return "; ".join(EXIT_REASON_TR.get(r, r) for r in c.reasons)


ACTION_ICON = {ExitAction.SELL: "🔴", ExitAction.TAKE_PARTIAL: "🟠", ExitAction.HOLD: "🟢"}


def holding_lines(h: Holding, c: ExitCheck, result) -> list[str]:
    s = _signal_of(result, c.symbol)
    q = _live(result, c.symbol)
    reason = f" – {esc(exit_reason(c))}" if c.reasons else ""
    lines = [
        f"{ACTION_ICON[c.action]} <b>{esc(c.symbol)}</b> · <b>{ACTION_TR[c.action]}</b>{reason}"
    ]
    price = f"son {money(c.last_close)}" if c.last_close is not None else "fiyat yok"
    if q:
        price += f" · anlık {money(q.price)}"
    pnl = f" · K/Z {pct(c.pnl_pct)} ({money(c.pnl_try, 0)} TL)" if c.pnl_pct is not None else ""
    lines.append(f"   alış {money(h.entry_price)} × {money(h.shares, 0)} · {price}{pnl}")
    if c.stop is not None:
        lines.append(f"   stop {money(c.stop)} · TP1 {money(c.tp1)} · TP2 {money(c.tp2)}")
    if s:
        lines.append(f"   sinyal {signal_text(s.signal)} (puan {money(s.score, 1)})")
    return lines


def portfolio_text(holdings: list[Holding], report) -> str:
    if not holdings:
        return "Portföyünüzde pozisyon yok. Uygulamadaki Portföyüm sekmesinden ekleyebilirsiniz."
    if report is None or len(report.checks) != len(holdings):
        return "Portföy henüz kontrol edilmedi."
    lines = [f"<b>Portföyüm · {report.scan.as_of:%d.%m.%Y} kapanışı</b>"]
    for h, c in zip(holdings, report.checks, strict=True):
        lines += holding_lines(h, c, report.scan)
    sells = sum(c.action is ExitAction.SELL for c in report.checks)
    partial = sum(c.action is ExitAction.TAKE_PARTIAL for c in report.checks)
    total = sum(c.pnl_try or 0 for c in report.checks)
    lines.append(
        f"\n{sells} SAT, {partial} KISMİ SAT, {len(holdings) - sells - partial} TUT · "
        f"toplam açık K/Z {money(total, 0)} TL"
    )
    lines.append("SAT kararı ertesi seansın açılışında uygulanacak şekilde hesaplanır.")
    return "\n".join(lines) + _synthetic_tag(report.scan)


def status_text(
    result,
    report,
    holdings: list[Holding],
    last_update: datetime | None,
    auto_minutes: int | None,
    provider: str,
) -> str:
    names = {"yahoo": "Yahoo Finance", "synthetic": "Sentetik demo", "csv": "CSV dosyaları"}
    lines = ["<b>BISTWatcher açık</b>"]
    lines.append(f"Veri: {names.get(provider, provider)}")
    lines.append(
        f"Otomatik yenileme: {auto_minutes} dk" if auto_minutes else "Otomatik yenileme kapalı"
    )
    if last_update:
        lines.append(f"Son güncelleme: {last_update:%d.%m %H:%M}")
    if result is not None:
        buys = sum(s.signal in BUY_SIGNALS for s in result.signals)
        lines += [
            f"Son tarama: {result.as_of:%d.%m.%Y} kapanışı, {len(result.signals)} hisse, {buys} AL",
            regime_line(result),
        ]
        reasons = "; ".join(tr(x) for x in result.regime.reasons)
        if reasons:
            lines.append(f"<i>{esc(reasons)}</i>")
    else:
        lines.append("Henüz tarama yapılmadı.")
    if holdings:
        if report is not None and len(report.checks) == len(holdings):
            acts = [ACTION_TR[c.action] for c in report.checks]
            lines.append(
                f"Portföy: {len(holdings)} pozisyon · "
                + ", ".join(f"{a} {acts.count(a)}" for a in dict.fromkeys(acts))
            )
        else:
            lines.append(f"Portföy: {len(holdings)} pozisyon (kontrol edilmedi)")
    return "\n".join(lines) + _synthetic_tag(result)


def analysis_caption(result, symbol: str, holding: Holding | None = None) -> str:
    from bist_quant.gui.report import held_view

    s = _signal_of(result, symbol)
    if s is None:
        why = result.skipped.get(symbol, "veri bulunamadı") if result is not None else ""
        return f"<b>{esc(symbol)}</b> analiz edilemedi ({esc(why)})."
    q = _live(result, symbol)
    lines = [f"<b>{esc(s.symbol)}</b> · {money(s.close)} TL ({s.date:%d.%m.%Y} kapanışı)"]
    if q:
        lines.append(
            f"Anlık {money(q.price)} ({pct(100 * (q.price / s.close - 1))}, "
            f"{q.time:%H:%M}, ~15 dk gecikmeli)"
        )
    lines.append(
        f"Sinyal: <b>{signal_text(s.signal)}</b> · puan {money(s.score, 1)}/100 "
        f"(AL eşiği {s.buy_threshold:.0f})"
    )
    lines.append(f"Elindeyse: <b>{held_view(result, s)}</b>")
    if s.signal in PLAN_SIGNALS and s.risk:
        p = s.risk
        lines.append(
            f"Giriş {money(p.entry_zone_low)}–{money(p.entry_zone_high)} · stop {money(p.stop)}"
        )
        lines.append(
            f"TP1 {money(p.tp1)} · TP2 {money(p.tp2)} · G/R {money(p.risk_reward, 2)} · "
            f"{money(p.shares, 0)} lot"
        )
    if holding:
        lines.append(f"Alışınız: {money(holding.entry_price)} × {money(holding.shares, 0)}")
    return "\n".join(lines)


def analysis_details(result, symbol: str) -> str:
    s = _signal_of(result, symbol)
    if s is None:
        return ""
    ex = s.explanation
    lines = []
    comps = [c for c in s.components.values() if c.enabled]
    if comps:
        from bist_quant.gui.texts import COMPONENT_TR

        lines.append("<b>Puan dağılımı</b>")
        for k, c in s.components.items():
            if c.enabled:
                lines.append(
                    f"{esc(COMPONENT_TR.get(k, k))}: {money(c.points, 1)}/{c.max_points:.0f}"
                )
    for title, items in (
        ("Olumlu", ex.positive_factors),
        ("Olumsuz", ex.negative_factors),
        ("Filtreler", ex.filters),
    ):
        if items:
            lines += ["", f"<b>{title}</b>"] + [f"• {esc(tr(x))}" for x in items[:8]]
    return "\n".join(lines) + _synthetic_tag(result)


def chart_png(result, symbol: str, entry_price: float | None = None, sessions: int = 120) -> bytes:
    """The app's analysis chart, rendered off-screen (thread-safe, no Qt)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from bist_quant.gui import charts

    s = _signal_of(result, symbol)
    fig = Figure(figsize=(10, 7.5), dpi=100)
    FigureCanvasAgg(fig)
    charts.price_chart(
        fig,
        result.features[symbol],
        symbol,
        s.risk if s is not None and s.signal in PLAN_SIGNALS else None,
        sessions,
        entry_price=entry_price,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


# ------------------------------------------------------------------ alerts


@dataclass
class AlertState:
    """What has been reported already; stored in ``telegram_durum.json``."""

    holdings: dict[str, dict] = field(default_factory=dict)  # symbol -> {karar, sinyal}
    buys_as_of: str | None = None
    buys: list[str] = field(default_factory=list)
    levels: dict[str, str] = field(default_factory=dict)  # "SYM:stop" -> ISO date alerted


def load_state(home: Path) -> AlertState:
    try:
        raw = json.loads((home / STATE_FILE).read_text(encoding="utf-8"))
        known = {f.name for f in fields(AlertState)}
        return AlertState(**{k: v for k, v in raw.items() if k in known})
    except (OSError, ValueError, TypeError):
        return AlertState()


def save_state(home: Path, state: AlertState) -> None:
    (home / STATE_FILE).write_text(
        json.dumps(asdict(state), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def portfolio_alerts(state: AlertState, holdings: list[Holding], report) -> list[str]:
    """Held positions whose decision (SAT / KISMİ SAT / TUT) or scan signal changed.

    The first time a position is seen, only an actionable decision (SAT, KISMİ SAT) is
    reported; TUT is recorded silently.
    """
    if report is None or len(report.checks) != len(holdings):
        return []
    out = []
    seen = set()
    for h, c in zip(holdings, report.checks, strict=True):
        if "no_data" in c.reasons or "before_entry" in c.reasons:
            continue
        seen.add(c.symbol)
        s = _signal_of(report.scan, c.symbol)
        now = {"karar": ACTION_TR[c.action], "sinyal": signal_text(s.signal) if s else "-"}
        prev = state.holdings.get(c.symbol)
        state.holdings[c.symbol] = now
        if prev == now:
            continue
        if prev is None and c.action is ExitAction.HOLD:
            continue
        changes = []
        if prev is None or prev.get("karar") != now["karar"]:
            before = f"{prev['karar']} → " if prev else ""
            changes.append(f"karar {before}<b>{now['karar']}</b>")
        if prev is not None and prev.get("sinyal") != now["sinyal"]:
            changes.append(f"sinyal {prev.get('sinyal')} → <b>{now['sinyal']}</b>")
        head = f"{ACTION_ICON[c.action]} <b>{esc(c.symbol)}</b> (portföyünüzde): " + ", ".join(
            changes
        )
        out.append("\n".join([head, *holding_lines(h, c, report.scan)[1:]]))
    for sym in list(state.holdings):
        if sym not in seen and sym not in {h.symbol for h in holdings}:
            del state.holdings[sym]
    if out:
        out[-1] += _synthetic_tag(report.scan)
    return out


def level_alerts(state: AlertState, holdings: list[Holding], report, today: date) -> list[str]:
    """Intraday (delayed) price at or beyond a held position's stop, TP1 or TP2.

    Decisions use closing prices; this is an early warning, sent once per level per day.
    """
    if report is None or len(report.checks) != len(holdings) or not report.scan.live_quotes:
        return []
    out = []
    for h, c in zip(holdings, report.checks, strict=True):
        q = report.scan.live_quotes.get(c.symbol)
        if not q or c.stop is None or c.action is ExitAction.SELL:
            continue
        hits = []
        if q.price <= c.stop:
            hits.append(("stop", f"stop seviyesinin ({money(c.stop)}) altında"))
        elif c.tp2 is not None and q.price >= c.tp2:
            hits.append(("tp2", f"2. hedefin (TP2 {money(c.tp2)}) üzerinde"))
        elif c.tp1 is not None and q.price >= c.tp1 and not h.tp1_taken:
            hits.append(("tp1", f"1. hedefin (TP1 {money(c.tp1)}) üzerinde"))
        for code, text in hits:
            key = f"{c.symbol}:{code}"
            if state.levels.get(key) == today.isoformat():
                continue
            state.levels[key] = today.isoformat()
            icon = "🔴" if code == "stop" else "🎯"
            out.append(
                f"{icon} <b>{esc(c.symbol)}</b> anlık {money(q.price)} ({q.time:%H:%M}) "
                f"{text}. Karar seans kapanışında kesinleşir."
            )
    for key in [k for k, d in state.levels.items() if d < today.isoformat()]:
        del state.levels[key]
    return out


def buy_alerts(state: AlertState, result) -> list[str]:
    """AL / GÜÇLÜ AL signals that were not reported yet for this or the previous session."""
    if result is None:
        return []
    as_of = result.as_of.isoformat()
    if state.buys_as_of and as_of < state.buys_as_of:
        return []  # an older session (e.g. a historical scan); never alert on it
    current = [s for s in result.signals if s.signal in BUY_SIGNALS]
    known = set(state.buys)
    new = [s for s in current if s.symbol not in known]
    if as_of == state.buys_as_of:
        state.buys = sorted(known | {s.symbol for s in current})
    else:
        state.buys = sorted(s.symbol for s in current)
    state.buys_as_of = as_of
    if not new:
        return []
    lines = [f"🟢 <b>Yeni AL sinyali · {result.as_of:%d.%m.%Y} kapanışı</b>", regime_line(result)]
    lines += [signal_line(result, s) for s in new]
    lines.append("Giriş ertesi seansta, giriş bölgesinde düşünülür. Grafik: /analiz SEMBOL")
    return ["\n".join(lines) + _synthetic_tag(result)]
