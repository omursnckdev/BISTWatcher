"""Qt side of the Telegram bot: the settings tab and the glue to the main window.

Commands arrive on the bot's polling thread and are handed to the Qt thread with a
signal, where the window's current results are read. Anything slow (a fresh analysis
scan, chart rendering) runs on a background thread and replies when done.
"""

from __future__ import annotations

import contextlib
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from bist_quant.data.market_data import ISTANBUL_TZ
from bist_quant.data.universe import load_universe, normalize_symbol
from bist_quant.gui import services
from bist_quant.gui import telegram_bot as tb


class TelegramController(QObject):
    message = Signal(object)  # tb.Incoming, from the polling thread
    status = Signal(str, bool)  # text, running ok
    chat_seen = Signal(int, str)  # an unauthorised chat sent /start

    def __init__(self, window) -> None:
        super().__init__(window)
        self.win = window
        self.home = window.home
        self.cfg = tb.load_config(self.home)
        self.state = tb.load_state(self.home)
        self.runner: tb.BotRunner | None = None
        self.reply_after_scan: set[int] = set()
        self.reply_after_portfolio: set[int] = set()
        self.last_status = ("Kapalı", False)
        self.message.connect(self._handle)
        self.status.connect(self._remember_status)

    # ------------------------------------------------------------ lifecycle
    @property
    def active(self) -> bool:
        return self.runner is not None and bool(self.cfg.chat_id)

    def start(self) -> None:
        self.stop()
        if not self.cfg.ready:
            self.status.emit("Kapalı" if not self.cfg.enabled else "Token girilmedi", False)
            return
        self.status.emit("Bağlanıyor...", False)
        self.runner = tb.BotRunner(self.cfg, self.message.emit, self.status.emit)
        self.runner.start()

    def stop(self) -> None:
        if self.runner:
            self.runner.stop()
            self.runner = None

    def apply(self, cfg: tb.TelegramConfig) -> None:
        self.cfg = cfg
        tb.save_config(self.home, cfg)
        self.start()

    def send_test(self) -> bool:
        if not self.active:
            return False
        self.runner.send(
            "✅ BISTWatcher test mesajı. Bildirimler bu sohbete gelecek.\n\n" + tb.help_text()
        )
        return True

    def _remember_status(self, text: str, ok: bool) -> None:
        self.last_status = (text, ok)

    def _quietly(self, fn) -> None:
        """Start a window task whose errors go to the status bar, not a dialog box
        (nobody may be at the computer to close it)."""
        self.win._quiet = True
        try:
            fn()
        finally:
            self.win._quiet = False

    def _save_state(self) -> None:
        with contextlib.suppress(OSError):
            tb.save_state(self.home, self.state)

    # ------------------------------------------------------------ hooks from the window
    def on_scan(self, result, historical: bool) -> None:
        """A scan finished (manual or automatic)."""
        if not self.active:
            return
        for chat in self.reply_after_scan:
            self.runner.send(tb.scan_text(result), chat)
        self.reply_after_scan.clear()
        if historical:
            return
        if self.cfg.notify_buys:
            for text in tb.buy_alerts(self.state, result):
                self.runner.send(text)
            self._save_state()
        # Decision alerts need the portfolio checked against this session's close.
        w = self.win
        stale = w.portfolio_result is None or w.portfolio_result.scan.as_of < result.as_of
        if w.holdings and stale and w.portfolio_button.isEnabled():
            self._quietly(w.start_portfolio)

    def on_portfolio(self, report) -> None:
        """The portfolio was checked, or its intraday prices refreshed."""
        if not self.active:
            return
        holdings = self.win.holdings
        for chat in self.reply_after_portfolio:
            self.runner.send(tb.portfolio_text(holdings, report), chat)
        self.reply_after_portfolio.clear()
        texts = []
        if self.cfg.notify_portfolio:
            texts += tb.portfolio_alerts(self.state, holdings, report)
        if self.cfg.notify_levels:
            today = datetime.now(ISTANBUL_TZ).date()
            texts += tb.level_alerts(self.state, holdings, report, today)
        for text in texts:
            self.runner.send(text)
        self._save_state()

    # ------------------------------------------------------------ commands
    def _handle(self, msg: tb.Incoming) -> None:
        if not self.runner:
            return
        cmd = tb.parse_command(msg.text)
        if not msg.authorized:
            if cmd and cmd[0] == "start":
                self.runner.send(
                    tb.welcome_text(msg.chat_id, False, bool(self.cfg.chat_id)), msg.chat_id
                )
                self.chat_seen.emit(msg.chat_id, msg.name)
            return
        send = lambda text: self.runner.send(text, msg.chat_id)  # noqa: E731
        if cmd is None:
            send("Komutlar için /yardim yazın.")
            return
        name, args = cmd
        w = self.win
        if name == "start":
            send(tb.welcome_text(msg.chat_id, True, True))
        elif name == "yardim":
            send(tb.help_text())
        elif name == "durum":
            minutes = w.prefs.refresh_minutes if w.prefs.auto_refresh else None
            send(
                tb.status_text(
                    w.scan_result,
                    w.portfolio_result,
                    w.holdings,
                    w.last_update,
                    minutes,
                    w.prefs.provider,
                )
            )
        elif name == "portfoy":
            fresh = w.portfolio_result is not None and len(w.portfolio_result.checks) == len(
                w.holdings
            )
            if w.holdings and not fresh:
                send("Portföy kontrol ediliyor, sonuç birazdan gelecek...")
                self.reply_after_portfolio.add(msg.chat_id)
                if w.portfolio_button.isEnabled():
                    self._quietly(w.start_portfolio)
            else:
                send(tb.portfolio_text(w.holdings, w.portfolio_result))
        elif name == "tara":
            top = int(args[0]) if args and args[0].isdigit() else 10
            if w.scan_result is None:
                send("Henüz tarama yok, başlatıyorum. Bitince sonuç gelecek.")
                self.reply_after_scan.add(msg.chat_id)
                self._quietly(w.start_scan)
            else:
                send(tb.scan_text(w.scan_result, top))
        elif name == "yenile":
            if w.workers:
                send("Uygulama şu an bir işlem yapıyor; biraz sonra tekrar deneyin.")
                return
            send("Tarama başlatıldı. Bitince sonuç ve varsa yeni sinyaller gelecek.")
            self.reply_after_scan.add(msg.chat_id)
            self._quietly(w.start_scan)
            if w.holdings:
                self.reply_after_portfolio.add(msg.chat_id)
                self._quietly(w.start_portfolio)
        elif name == "analiz":
            if not args:
                send("Hangi hisse? Örnek: /analiz BRSAN")
                return
            self._analysis(normalize_symbol(args[0]), msg.chat_id)
        else:
            send("Bilinmeyen komut. /yardim yazın.")

    def _analysis(self, symbol: str, chat_id: int) -> None:
        w = self.win
        runner = self.runner
        holding = next((h for h in w.holdings if h.symbol == symbol), None)
        result = None
        for r in (w.scan_result, w.portfolio_result.scan if w.portfolio_result else None):
            if r is not None and symbol in r.features and tb._signal_of(r, symbol):
                result = r
                break
        settings = None
        if result is None:
            runner.send(f"{symbol} analiz ediliyor...", chat_id)
            settings = w.settings()

        def work() -> None:
            res = result
            try:
                if res is None:
                    breadth = load_universe(settings.universe, None)
                    res = services.scan(settings, [symbol], breadth)
                caption = tb.analysis_caption(res, symbol, holding)
                if symbol not in res.features or tb._signal_of(res, symbol) is None:
                    runner.send(caption, chat_id)
                    return
                png = tb.chart_png(res, symbol, holding.entry_price if holding else None)
                runner.send_photo(png, caption, chat_id)
                details = tb.analysis_details(res, symbol)
                if details:
                    runner.send(details, chat_id)
            except Exception as exc:  # noqa: BLE001 - reported to the chat
                runner.send(f"{symbol} analiz edilemedi: {tb.esc(exc)}", chat_id)

        threading.Thread(target=work, daemon=True, name="telegram-analiz").start()


SETUP_HELP = """
<b>Kurulum (bir kez)</b>
<ol>
<li>Telegram'da <b>@BotFather</b>'ı açın, <code>/newbot</code> yazın, bota bir ad ve
sonu <i>bot</i> ile biten bir kullanıcı adı verin.</li>
<li>BotFather'ın verdiği <b>token</b>'ı aşağıya yapıştırın, <b>Botu etkinleştir</b>'i
işaretleyip <b>Kaydet ve başlat</b>'a basın.</li>
<li>Telegram'da kendi botunuzu açıp <code>/start</code> yazın. Sohbet kimliğiniz burada
görünür; <b>Bu sohbeti kullan</b>'a basın.</li>
<li><b>Test mesajı gönder</b> ile deneyin.</li>
</ol>
<b>Komutlar:</b> /portfoy, /tara, /analiz BRSAN, /durum, /yenile, /yardim<br>
Bot, bu uygulama açıkken çalışır (bilgisayar uyku modunda olmamalı). Yalnızca
yetkilendirdiğiniz sohbet yanıt alır. Token, veri klasöründeki telegram.json dosyasında
saklanır; kimseyle paylaşmayın.
"""


class TelegramTab(QWidget):
    def __init__(self, controller: TelegramController) -> None:
        super().__init__()
        self.ctl = controller
        cfg = controller.cfg
        outer = QHBoxLayout(self)
        box = QGroupBox("Telegram botu")
        form = QFormLayout(box)
        self.enabled = QCheckBox("Botu etkinleştir")
        self.enabled.setChecked(cfg.enabled)
        self.token = QLineEdit(cfg.token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("123456789:ABC... (BotFather'dan)")
        show = QCheckBox("göster")
        show.toggled.connect(
            lambda on: self.token.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        token_row = QHBoxLayout()
        token_row.addWidget(self.token)
        token_row.addWidget(show)
        self.chat = QLineEdit("" if cfg.chat_id is None else str(cfg.chat_id))
        self.chat.setPlaceholderText("Bota /start yazınca burada görünür")
        self.chat.setMaximumWidth(220)
        self.seen = QLabel("")
        self.seen.setWordWrap(True)
        self.use_seen = QPushButton("Bu sohbeti kullan")
        self.use_seen.hide()
        self.use_seen.clicked.connect(self._use_seen)
        self._seen_id: int | None = None
        seen_row = QHBoxLayout()
        seen_row.addWidget(self.seen, 1)
        seen_row.addWidget(self.use_seen)
        self.n_portfolio = QCheckBox(
            "Portföyümdeki hisselerin karar/sinyal değişimleri (TUT → SAT)"
        )
        self.n_portfolio.setChecked(cfg.notify_portfolio)
        self.n_levels = QCheckBox("Seans içinde fiyat stop'a ya da hedefe gelince erken uyarı")
        self.n_levels.setChecked(cfg.notify_levels)
        self.n_buys = QCheckBox("Tarama sonrası yeni AL sinyalleri")
        self.n_buys.setChecked(cfg.notify_buys)
        self.state_label = QLabel("")
        self.state_label.setTextFormat(Qt.TextFormat.RichText)
        form.addRow("", self.enabled)
        form.addRow("Bot token", token_row)
        form.addRow("Sohbet kimliği (chat id)", self.chat)
        form.addRow("", seen_row)
        form.addRow("Bildirimler", self.n_portfolio)
        form.addRow("", self.n_levels)
        form.addRow("", self.n_buys)
        form.addRow("Durum", self.state_label)
        buttons = QHBoxLayout()
        save = QPushButton("Kaydet ve başlat")
        save.setStyleSheet("font-weight:bold;padding:6px 18px")
        save.clicked.connect(self.save)
        test = QPushButton("Test mesajı gönder")
        test.clicked.connect(self.test)
        buttons.addWidget(save)
        buttons.addWidget(test)
        buttons.addStretch()
        form.addRow(buttons)
        outer.addWidget(box, 3)
        guide = QTextBrowser()
        guide.setHtml(SETUP_HELP)
        side = QVBoxLayout()
        side.addWidget(guide)
        outer.addLayout(side, 2)
        controller.status.connect(self._show_status)
        controller.chat_seen.connect(self._chat_seen)
        self._show_status(*controller.last_status)

    def config(self) -> tb.TelegramConfig:
        return tb.TelegramConfig(
            enabled=self.enabled.isChecked(),
            token=self.token.text().strip(),
            chat_id=tb.parse_chat_id(self.chat.text()),
            notify_portfolio=self.n_portfolio.isChecked(),
            notify_buys=self.n_buys.isChecked(),
            notify_levels=self.n_levels.isChecked(),
        )

    def save(self) -> None:
        self.ctl.apply(self.config())

    def test(self) -> None:
        cfg = self.config()
        if cfg != self.ctl.cfg:
            self.ctl.apply(cfg)
        if not self.ctl.send_test():
            self._show_status(
                "Önce token ve sohbet kimliğini girip botu etkinleştirin (Kaydet ve başlat).",
                False,
            )

    def _show_status(self, text: str, ok: bool) -> None:
        color = "#1b8a3a" if ok else "#c62828"
        extra = "" if not ok or self.ctl.cfg.chat_id else " · sohbet kimliği bekleniyor"
        self.state_label.setText(f"<b style='color:{color}'>{text}</b>{extra}")

    def _chat_seen(self, chat_id: int, name: str) -> None:
        self._seen_id = chat_id
        who = f"{name} " if name else ""
        self.seen.setText(f"/start gönderen: {who}(kimlik {chat_id})")
        self.use_seen.show()

    def _use_seen(self) -> None:
        if self._seen_id is None:
            return
        self.chat.setText(str(self._seen_id))
        self.enabled.setChecked(True)
        self.ctl.apply(self.config())
        self.use_seen.hide()
        self.seen.setText("Sohbet yetkilendirildi.")
        self.ctl.send_test()
