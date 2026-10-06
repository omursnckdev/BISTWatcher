"""BISTWatcher desktop application (PySide6).

Run with ``python -m bist_quant.gui`` or the ``bistwatcher`` script; the Windows
build is ``BISTWatcher.exe``. All computation happens in
:mod:`bist_quant.gui.services` on a worker thread; this module only shows results.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

from bist_quant import __version__

# A windowed (no console) Windows build has no stdout/stderr; the engine may print.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115

import matplotlib  # noqa: E402

matplotlib.use("QtAgg")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PySide6.QtCore import (  # noqa: E402
    QAbstractTableModel,
    QDate,
    QLocale,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from bist_quant.data.market_data import ISTANBUL_TZ  # noqa: E402
from bist_quant.data.universe import load_universe, normalize_symbol  # noqa: E402
from bist_quant.gui import charts, paths, report, services  # noqa: E402
from bist_quant.gui.prefs import (  # noqa: E402
    Prefs,
    load_holdings,
    load_prefs,
    save_holdings,
    save_prefs,
)
from bist_quant.gui.texts import (  # noqa: E402
    ACTION_COLOR,
    ACTION_TR,
    COMPONENT_TR,
    REGIME_TR,
    SELL_COLOR,
    SIGNAL_COLOR,
    money,
    regime_text,
    signal_text,
    tr,
)
from bist_quant.models.signals import MarketRegime, SignalType  # noqa: E402
from bist_quant.strategy.exit_check import ExitAction, Holding  # noqa: E402

APP_NAME = "BISTWatcher"
REGIME_COLOR = {
    MarketRegime.BULL: "#1b8a3a",
    MarketRegime.NEUTRAL: "#6c757d",
    MarketRegime.BEAR: "#c62828",
    MarketRegime.HIGH_VOLATILITY: "#ef6c00",
}
FILTERS = ["Tümü", "Sadece AL", "AL + Zayıf kurulum", "Elindeyse SAT uyarıları", "İzle ve üstü"]


# ---------------------------------------------------------------- helpers


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs

    def run(self) -> None:
        try:
            self.done.emit(self.fn(*self.args, **self.kwargs))
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.failed.emit(f"{exc}\n\n{traceback.format_exc(limit=4)}")


class RowsModel(QAbstractTableModel):
    """Table model over a list of dicts; ``colors`` maps column -> {value: colour}."""

    def __init__(self, rows: list[dict] | None = None, colors: dict | None = None) -> None:
        super().__init__()
        self.rows: list[dict] = []
        self.cols: list[str] = []
        self.colors = colors or {}
        self.set_rows(rows or [])

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.cols = list(rows[0]) if rows else []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.cols[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        col = self.cols[index.column()]
        value = self.rows[index.row()][col]
        if role == Qt.ItemDataRole.DisplayRole:
            if value is None or (isinstance(value, float) and value != value):
                return "-"
            if isinstance(value, float):
                return money(
                    value,
                    1
                    if col in {"Puan", "G/R", "R", "K/Z %", "Günlük %", "Anlık %", "Özkaynak %"}
                    else 2,
                )
            if isinstance(value, int) and not isinstance(value, bool) and col != "Sıra":
                return money(value, 0)
            return str(value)
        if role == Qt.ItemDataRole.UserRole:  # sort key
            if value is None:
                return float("-inf")
            return value
        if role == Qt.ItemDataRole.BackgroundRole:
            color = self.colors.get(col, {}).get(str(value))
            if color:
                return QColor(color)
            if col in {"Günlük %", "Anlık %", "K/Z %", "K/Z TL", "R"} and isinstance(
                value, int | float
            ):
                return QColor("#e8f5e9") if value > 0 else QColor("#ffebee") if value < 0 else None
        if role == Qt.ItemDataRole.ForegroundRole and self.colors.get(col, {}).get(str(value)):
            return QColor("#000000")
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        if role == Qt.ItemDataRole.TextAlignmentRole and numeric:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def row(self, i: int) -> dict:
        return self.rows[i]


class SortProxy(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def lessThan(self, a, b) -> bool:  # noqa: N802
        x, y = a.data(Qt.ItemDataRole.UserRole), b.data(Qt.ItemDataRole.UserRole)
        try:
            return x < y
        except TypeError:
            return str(x) < str(y)


def make_table(model: RowsModel) -> tuple[QTableView, SortProxy]:
    proxy = SortProxy()
    proxy.setSourceModel(model)
    view = QTableView()
    view.setModel(proxy)
    view.setSortingEnabled(True)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setAlternatingRowColors(True)
    view.verticalHeader().setVisible(False)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    reset_sort(view)
    return view, proxy


def reset_sort(view: QTableView) -> None:
    """Show rows in the order they were given (until the user clicks a header)."""
    view.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    view.model().sort(-1)


def selected_row(view: QTableView, proxy: SortProxy, model: RowsModel) -> dict | None:
    idx = view.selectionModel().selectedRows() if view.selectionModel() else []
    if not idx:
        return None
    return model.row(proxy.mapToSource(idx[0]).row())


class Canvas(FigureCanvasQTAgg):
    def __init__(self, width: float = 8, height: float = 6) -> None:
        self.figure = Figure(figsize=(width, height), dpi=100)
        super().__init__(self.figure)


def signal_colors() -> dict[str, dict[str, str]]:
    return {
        "Sinyal": {signal_text(k): v for k, v in SIGNAL_COLOR.items()},
        "Güncel Sinyal": {signal_text(k): v for k, v in SIGNAL_COLOR.items()},
        "Elindeyse": {"SAT": SELL_COLOR, "TUT": "#cfe8cf"},
        "Karar": {ACTION_TR[a]: ACTION_COLOR[a] for a in ExitAction},
    }


def backtest_info(summary, trades: int) -> str:
    """Requested vs simulated period, capital and why some signals were not traded."""
    req = ""
    if summary.requested_start and summary.requested_end:
        req = f"İstenen dönem {summary.requested_start:%d.%m.%Y} – {summary.requested_end:%d.%m.%Y}"
        if summary.start > summary.requested_start + timedelta(days=7):
            req += " (daha öncesi için yeterli veri yok)"
        req += "; "
    st = summary.stats
    text = (
        f"<b>{req}simüle edilen {summary.start:%d.%m.%Y} – {summary.end:%d.%m.%Y}</b> · "
        f"{summary.symbols} hisse · başlangıç sermayesi {money(summary.initial_equity, 0)} TL · "
        f"{trades} işlem. AL sinyali {st.get('orders', 0)}, gerçekleşen {st.get('filled', 0)}, "
        f"pozisyon/risk sınırı nedeniyle alınmayan {st.get('no_capacity', 0)}, "
        f"giriş bölgesine gelmeyen {st.get('missed_limit', 0)}."
    )
    if summary.skipped:
        text += " Atlanan: " + ", ".join(sorted(summary.skipped)) + "."
    return text


# ---------------------------------------------------------------- main window


class MainWindow(QMainWindow):
    def __init__(self, home: Path | None = None) -> None:
        super().__init__()
        self.home = home or paths.user_home()
        self.prefs = load_prefs(self.home)
        self.holdings = load_holdings(self.home)
        self.workers: list[Worker] = []
        self.scan_result = None
        self.analysis_result = None
        self.portfolio_result = None
        self.backtest_result = None

        self.setWindowTitle(f"{APP_NAME} {__version__}  ·  BIST tarama ve sinyal aracı")
        self.resize(1400, 860)

        self.regime_label = QLabel("Piyasa durumu: henüz tarama yapılmadı")
        self.regime_label.setTextFormat(Qt.TextFormat.RichText)
        self.regime_label.setStyleSheet("padding:6px;font-size:13px")
        self.tabs = QTabWidget()
        self.tabs.addTab(self._scan_tab(), "Tarama")
        self.tabs.addTab(self._analysis_tab(), "Hisse Analizi")
        self.tabs.addTab(self._portfolio_tab(), "Portföyüm (SAT/TUT)")
        self.tabs.addTab(self._backtest_tab(), "Geri Test")
        self.tabs.addTab(self._settings_tab(), "Ayarlar")
        self.tabs.addTab(self._help_tab(), "Yardım")

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.regime_label)
        lay.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(200)
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.refresh_label = QLabel("")
        self.statusBar().addPermanentWidget(self.refresh_label)
        self.last_update: datetime | None = None
        self._quiet = False
        self.auto_scan_skip: date | None = None  # a session that did not show up (holiday)
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self.auto_tick)
        self._configure_auto_refresh()
        self._update_source_status()

        menu = self.menuBar().addMenu("Dosya")
        open_dir = QAction("Veri klasörünü aç", self)
        open_dir.triggered.connect(lambda: self._open_path(self.home))
        menu.addAction(open_dir)
        reports = QAction("Rapor klasörünü aç", self)
        reports.triggered.connect(lambda: self._open_path(paths.reports_dir(self.home)))
        menu.addAction(reports)
        menu.addSeparator()
        quit_action = QAction("Çıkış", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

    # ------------------------------------------------------------ plumbing
    def settings(self):
        return services.load_app_settings(self.home, self.prefs)

    def universe(self, settings=None) -> list[str]:
        return services.universe_symbols(settings or self.settings(), self.prefs)

    def run_task(self, label: str, fn, on_done, *args, buttons: list[QPushButton] = (), **kw):
        for b in buttons:
            b.setEnabled(False)
        self.progress.show()
        self.statusBar().showMessage(label)
        worker = Worker(fn, *args, **kw)
        quiet = self._quiet  # automatic refreshes report errors in the status bar only

        def finish() -> None:
            for b in buttons:
                b.setEnabled(True)
            if worker in self.workers:
                self.workers.remove(worker)
            if not self.workers:
                self.progress.hide()

        def ok(result) -> None:
            finish()
            self.statusBar().showMessage("Hazır", 5000)
            on_done(result)

        def fail(message: str) -> None:
            finish()
            if quiet:
                first = message.split("\n", 1)[0]
                self.statusBar().showMessage(f"Otomatik yenileme başarısız: {first}", 60_000)
                return
            self.statusBar().showMessage("Hata", 5000)
            self.show_error(message)

        worker.done.connect(ok)
        worker.failed.connect(fail)
        self.workers.append(worker)
        worker.start()
        return worker

    def show_error(self, message: str) -> None:
        first = message.split("\n", 1)[0]
        hint = ""
        if self.prefs.provider == "yahoo" and any(
            k in message.lower() for k in ("connect", "timeout", "unavailable", "http", "name")
        ):
            hint = (
                "\n\nİnternet bağlantınızı kontrol edin. Yahoo Finance'e erişilemiyorsa "
                "Ayarlar'dan 'Sentetik demo verisi' seçerek uygulamayı deneyebilirsiniz."
            )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("İşlem tamamlanamadı")
        box.setText(first + hint)
        box.setDetailedText(message)
        box.exec()

    def _open_path(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------ auto refresh
    def _configure_auto_refresh(self) -> None:
        if self.prefs.auto_refresh:
            self.auto_timer.start(max(1, int(self.prefs.refresh_minutes)) * 60_000)
        else:
            self.auto_timer.stop()
        self._update_refresh_label()

    def _update_refresh_label(self) -> None:
        last = f"son güncelleme {self.last_update:%H:%M:%S}" if self.last_update else ""
        if self.prefs.auto_refresh:
            nxt = datetime.now(ISTANBUL_TZ) + timedelta(
                milliseconds=self.auto_timer.remainingTime()
            )
            parts = [
                f"Otomatik yenileme: {self.prefs.refresh_minutes} dk",
                last,
                f"sonraki {nxt:%H:%M}",
            ]
        else:
            parts = ["Otomatik yenileme kapalı", last]
        self.refresh_label.setText("  ·  ".join(p for p in parts if p) + "  ")

    def _mark_updated(self) -> None:
        self.last_update = datetime.now(ISTANBUL_TZ)
        self._update_refresh_label()

    def auto_tick(self) -> None:
        """Periodic refresh: new scan when a session has closed, else intraday prices."""
        try:
            if self.workers or self.scan_use_date.isChecked():
                return
            now = datetime.now(ISTANBUL_TZ)
            as_of = self.scan_result.as_of if self.scan_result else None
            action = services.auto_refresh_action(now, as_of, self.prefs.provider)
            if action == "scan" and self.auto_scan_skip == services.last_completed_session(now):
                action = "quotes" if services.session_open(now) else "none"
            self._quiet = True
            if action == "scan":
                self.start_scan()
                if self.portfolio_result and self.holdings:
                    self.start_portfolio()
            elif action == "quotes":
                self.refresh_quotes()
                self.refresh_portfolio_quotes()
        finally:
            self._quiet = False
            self._update_refresh_label()

    def refresh_portfolio_quotes(self) -> None:
        if not self.portfolio_result:
            return
        report_ = self.portfolio_result

        def done(scan) -> None:
            report_.scan = scan
            self._portfolio_done(report_)

        self.run_task(
            "Portföy fiyatları alınıyor...", services.refresh_live_quotes, done, report_.scan
        )

    def _update_source_status(self) -> None:
        names = {
            "yahoo": "Yahoo Finance (gerçek veri)",
            "synthetic": "Sentetik demo verisi",
            "csv": "Kendi CSV dosyalarım",
        }
        self.statusBar().showMessage(f"Veri kaynağı: {names.get(self.prefs.provider)}")

    def _set_regime(self, result) -> None:
        r = result.regime
        color = REGIME_COLOR.get(r.regime, "#555")
        thr = f" · AL eşiği <b>{result.signals[0].buy_threshold:.0f}</b>" if result.signals else ""
        breadth = f" · genişlik %{r.breadth_pct:.0f}" if r.breadth_pct is not None else ""
        demo = (
            " · <b style='color:#c62828'>SENTETİK VERİ</b>"
            if result.provider == "synthetic"
            else ""
        )
        self.regime_label.setText(
            f"<b>{result.as_of:%d.%m.%Y}</b> · {r.index} piyasa durumu: "
            f"<span style='background:{color};color:white;padding:2px 8px'>&nbsp;"
            f"{regime_text(r.regime)}&nbsp;</span> (puan {r.score.points:.1f}/"
            f"{r.score.max_points:.0f}){breadth}{thr}{demo}<br>"
            f"<span style='color:#666'>{'; '.join(tr(x) for x in r.reasons)}</span>"
        )

    def _save_dialog(self, title: str, default_name: str, filt: str) -> Path | None:
        start = str(paths.reports_dir(self.home) / default_name)
        name, _ = QFileDialog.getSaveFileName(self, title, start, filt)
        return Path(name) if name else None

    # ------------------------------------------------------------ scan tab
    def _scan_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.scan_universe = QComboBox()
        self.scan_universe.addItems(["BIST30", "BIST50", "BIST100", "Özel liste"])
        self.scan_universe.setCurrentIndex(
            services.UNIVERSES.index(self.prefs.universe)
            if self.prefs.universe in services.UNIVERSES
            else 0
        )
        self.scan_use_date = QCheckBox("Geçmiş tarih:")
        self.scan_date = QDateEdit(QDate.currentDate())
        self.scan_date.setCalendarPopup(True)
        self.scan_date.setDisplayFormat("dd.MM.yyyy")
        self.scan_date.setEnabled(False)
        self.scan_use_date.toggled.connect(self.scan_date.setEnabled)
        self.scan_refresh = QCheckBox("Veriyi yeniden indir")
        self.scan_filter = QComboBox()
        self.scan_filter.addItems(FILTERS)
        self.scan_filter.currentIndexChanged.connect(self._fill_scan_table)
        self.scan_button = QPushButton("▶  Tara")
        self.scan_button.setStyleSheet("font-weight:bold;padding:6px 18px")
        self.scan_button.clicked.connect(self.start_scan)
        for label, widget in (
            ("Evren:", self.scan_universe),
            ("", self.scan_use_date),
            ("", self.scan_date),
            ("Göster:", self.scan_filter),
        ):
            if label:
                bar.addWidget(QLabel(label))
            bar.addWidget(widget)
        bar.addWidget(self.scan_refresh)
        bar.addStretch()
        self.quote_button = QPushButton("Anlık fiyatları yenile")
        self.quote_button.setToolTip(
            "Seans içi son fiyatları (Yahoo, ~15 dk gecikmeli) tabloya ekler. "
            "Puan ve sinyaller son tamamlanmış seansın kapanışına göre kalır."
        )
        self.quote_button.setEnabled(False)
        self.quote_button.clicked.connect(self.refresh_quotes)
        bar.addWidget(self.quote_button)
        self.export_buttons = []
        for text, fn in (
            ("Excel'e aktar", self.export_scan_excel),
            ("HTML rapor", self.export_scan_html),
            ("CSV", self.export_scan_csv),
        ):
            b = QPushButton(text)
            b.setEnabled(False)
            b.clicked.connect(fn)
            self.export_buttons.append(b)
            bar.addWidget(b)
        bar.addWidget(self.scan_button)
        lay.addLayout(bar)

        self.scan_summary = QLabel("Taramayı başlatmak için ▶ Tara'ya basın.")
        self.scan_summary.setWordWrap(True)
        lay.addWidget(self.scan_summary)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.scan_model = RowsModel(colors=signal_colors())
        self.scan_view, self.scan_proxy = make_table(self.scan_model)
        self.scan_view.selectionModel().selectionChanged.connect(self._scan_selected)
        self.scan_view.doubleClicked.connect(self._scan_open_analysis)
        split.addWidget(self.scan_view)
        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        self.scan_score_canvas = Canvas(4, 2.6)
        self.scan_detail = QTextBrowser()
        side_lay.addWidget(self.scan_score_canvas, 2)
        side_lay.addWidget(self.scan_detail, 3)
        open_btn = QPushButton("Grafikte aç (Hisse Analizi)")
        open_btn.clicked.connect(self._scan_open_analysis)
        side_lay.addWidget(open_btn)
        split.addWidget(side)
        split.setSizes([950, 420])
        lay.addWidget(split, 1)
        return w

    def start_scan(self) -> None:
        idx = self.scan_universe.currentIndex()
        self.prefs.universe = services.UNIVERSES[idx]
        save_prefs(self.home, self.prefs)
        try:
            settings = self.settings()
            symbols = self.universe(settings)
        except Exception as exc:  # noqa: BLE001
            self.show_error(str(exc))
            return
        as_of = self.scan_date.date().toPython() if self.scan_use_date.isChecked() else None
        breadth = load_universe(settings.universe, None)
        self.run_task(
            f"{len(symbols)} hisse taranıyor...",
            services.scan,
            self._scan_done,
            settings,
            symbols,
            breadth,
            as_of,
            self.scan_refresh.isChecked(),
            buttons=[self.scan_button],
        )

    def _scan_done(self, result) -> None:
        self.scan_result = result
        self._mark_updated()
        expected = services.last_completed_session(datetime.now(ISTANBUL_TZ))
        if result.as_of < expected and not self.scan_use_date.isChecked():
            self.auto_scan_skip = expected  # market holiday or data not published yet
        self._set_regime(result)
        self._set_scan_summary()
        for b in self.export_buttons:
            b.setEnabled(True)
        self._fill_scan_table()

    def _set_scan_summary(self) -> None:
        lines = report.summary_lines(self.scan_result)
        self.scan_summary.setText(" · ".join([lines[1], *lines[4:7]]))
        self.quote_button.setEnabled(self.prefs.provider == "yahoo")

    def refresh_quotes(self) -> None:
        if not self.scan_result:
            return

        def done(result) -> None:
            self.scan_result = result
            self._mark_updated()
            self._set_scan_summary()
            self._fill_scan_table()

        self.run_task(
            "Anlık fiyatlar alınıyor...",
            services.refresh_live_quotes,
            done,
            self.scan_result,
            buttons=[self.quote_button],
        )

    def _fill_scan_table(self) -> None:
        if not self.scan_result:
            return
        rows = report.signal_rows(self.scan_result)
        mode = self.scan_filter.currentIndex()
        buy = {signal_text(SignalType.STRONG_BUY_CANDIDATE), signal_text(SignalType.BUY_CANDIDATE)}
        if mode == 1:
            rows = [r for r in rows if r["Sinyal"] in buy]
        elif mode == 2:
            rows = [r for r in rows if r["Sinyal"] in buy | {signal_text(SignalType.WEAK_SETUP)}]
        elif mode == 3:
            rows = [r for r in rows if r["Elindeyse"] == "SAT"]
        elif mode == 4:
            rows = [r for r in rows if r["Sinyal"] != signal_text(SignalType.NO_TRADE)]
        self.scan_model.set_rows(rows)
        reset_sort(self.scan_view)
        if rows:
            self.scan_view.selectRow(0)

    def _signal_for(self, result, symbol: str):
        return next((s for s in result.signals if s.symbol == symbol), None) if result else None

    def _scan_selected(self) -> None:
        row = selected_row(self.scan_view, self.scan_proxy, self.scan_model)
        s = self._signal_for(self.scan_result, row["Sembol"]) if row else None
        if not s:
            return
        self._show_detail(s, self.scan_detail, self.scan_score_canvas, self.scan_result)

    def _show_detail(self, s, browser: QTextBrowser, canvas: Canvas, result) -> None:
        comps = [(k, c) for k, c in s.components.items() if c.enabled]
        charts.score_bars(
            canvas.figure,
            [COMPONENT_TR.get(k, k) for k, _ in comps],
            [c.points for _, c in comps],
            [c.max_points for _, c in comps],
        )
        canvas.draw_idle()
        color = SIGNAL_COLOR.get(s.signal, "#ccc")
        held = report.held_view(result, s)
        q = result.live_quotes.get(s.symbol)
        live = ""
        if q:
            chg = 100 * (q.price / s.close - 1) if s.close else 0.0
            live = (
                f"<p style='margin:2px 0'>Anlık: <b>{money(q.price)} TL</b> "
                f"({chg:+.2f}%, {q.time:%d.%m %H:%M}, ~15 dk gecikmeli). "
                "Puan ve sinyal seans kapanınca güncellenir.</p>"
            )
        parts = [
            f"<h3 style='margin:0'>{s.symbol} · {money(s.close)} TL "
            f"<small style='color:#666'>({s.date:%d.%m.%Y} kapanışı)</small></h3>",
            f"<p><span style='background:{color};padding:2px 6px'><b>{signal_text(s.signal)}</b>"
            f"</span> &nbsp;Puan <b>{s.score:.1f}</b>/100 &nbsp;(AL eşiği {s.buy_threshold:.0f})"
            f" &nbsp;· Elindeyse: <b style='color:{'#c62828' if held == 'SAT' else '#1b8a3a'}'>"
            f"{held}</b></p>",
            live,
        ]
        if s.risk:
            p = s.risk
            capped = " (dirençle sınırlı)" if p.resistance_capped else ""
            parts.append(
                "<table cellpadding='2'>"
                f"<tr><td>Giriş bölgesi</td><td><b>{money(p.entry_zone_low)} – "
                f"{money(p.entry_zone_high)}</b> (ertesi seans)</td></tr>"
                "<tr><td>Zarar-durdur</td>"
                f"<td><b style='color:#c62828'>{money(p.stop)}</b></td></tr>"
                f"<tr><td>Hedef 1 / 2</td><td><b>{money(p.tp1)} / {money(p.tp2)}</b></td></tr>"
                f"<tr><td>Getiri/Risk</td><td>{p.risk_reward:.2f}{capped}</td></tr>"
                f"<tr><td>Önerilen lot</td><td>{money(p.shares, 0)} adet = "
                f"{money(p.position_value, 0)} TL (risk {money(p.capital_at_risk, 0)} TL)</td></tr>"
                "</table>"
            )
        ex = s.explanation
        if ex.positive_factors:
            parts.append(
                "<b>Olumlu</b><ul>"
                + "".join(f"<li style='color:#1b5e20'>{tr(x)}</li>" for x in ex.positive_factors)
                + "</ul>"
            )
        if ex.negative_factors:
            parts.append(
                "<b>Olumsuz</b><ul>"
                + "".join(f"<li style='color:#b71c1c'>{tr(x)}</li>" for x in ex.negative_factors)
                + "</ul>"
            )
        if ex.notes:
            parts.append("<ul>" + "".join(f"<li>{tr(x)}</li>" for x in ex.notes) + "</ul>")
        if ex.filters:
            parts.append(
                "<b>Filtreler</b><ul>"
                + "".join(f"<li style='color:#e65100'>{tr(x)}</li>" for x in ex.filters)
                + "</ul>"
            )
        browser.setHtml("".join(parts))

    def _scan_open_analysis(self, *_):
        row = selected_row(self.scan_view, self.scan_proxy, self.scan_model)
        if not row or not self.scan_result:
            return
        s = self._signal_for(self.scan_result, row["Sembol"])
        self.analysis_symbol.setText(row["Sembol"])
        self.tabs.setCurrentIndex(1)
        self._analysis_done(self.scan_result, focus=s.symbol)

    def export_scan_excel(self) -> None:
        if not self.scan_result:
            return
        path = self._save_dialog(
            "Excel raporu kaydet", f"tarama_{self.scan_result.as_of:%Y%m%d}.xlsx", "Excel (*.xlsx)"
        )
        if path:
            report.export_scan_excel(self.scan_result, path, self._portfolio_rows())
            self._open_path(path)

    def export_scan_html(self) -> None:
        if not self.scan_result:
            return
        path = self._save_dialog(
            "HTML raporu kaydet", f"tarama_{self.scan_result.as_of:%Y%m%d}.html", "HTML (*.html)"
        )
        if path:
            report.export_scan_html(self.scan_result, path, self._portfolio_rows())
            self._open_path(path)

    def export_scan_csv(self) -> None:
        if not self.scan_result:
            return
        path = self._save_dialog(
            "CSV kaydet", f"tarama_{self.scan_result.as_of:%Y%m%d}.csv", "CSV (*.csv)"
        )
        if path:
            report.export_scan_csv(self.scan_result, path)

    # ------------------------------------------------------------ analysis tab
    def _analysis_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.analysis_symbol = QLineEdit()
        self.analysis_symbol.setPlaceholderText("Sembol, örn. THYAO")
        self.analysis_symbol.setMaximumWidth(180)
        try:
            names = load_universe(self.settings().universe, "BIST100")
        except Exception:  # noqa: BLE001
            names = []
        completer = QCompleter(names)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.analysis_symbol.setCompleter(completer)
        self.analysis_symbol.returnPressed.connect(self.start_analysis)
        self.analysis_sessions = QSpinBox()
        self.analysis_sessions.setRange(40, 750)
        self.analysis_sessions.setValue(160)
        self.analysis_sessions.setSuffix(" seans")
        self.analysis_sessions.valueChanged.connect(lambda _: self._redraw_analysis())
        self.analysis_button = QPushButton("▶  Analiz et")
        self.analysis_button.setStyleSheet("font-weight:bold;padding:6px 18px")
        self.analysis_button.clicked.connect(self.start_analysis)
        bar.addWidget(QLabel("Hisse:"))
        bar.addWidget(self.analysis_symbol)
        bar.addWidget(QLabel("Grafik:"))
        bar.addWidget(self.analysis_sessions)
        bar.addWidget(self.analysis_button)
        bar.addStretch()
        lay.addLayout(bar)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.analysis_canvas = Canvas(9, 7)
        split.addWidget(self.analysis_canvas)
        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        self.analysis_score_canvas = Canvas(4, 2.6)
        self.analysis_detail = QTextBrowser()
        side_lay.addWidget(self.analysis_score_canvas, 2)
        side_lay.addWidget(self.analysis_detail, 3)
        split.addWidget(side)
        split.setSizes([950, 420])
        lay.addWidget(split, 1)
        self._analysis_focus = None
        return w

    def start_analysis(self) -> None:
        sym = (
            normalize_symbol(self.analysis_symbol.text().strip())
            if self.analysis_symbol.text().strip()
            else ""
        )
        if not sym:
            return
        settings = self.settings()
        breadth = load_universe(settings.universe, None)
        self.run_task(
            f"{sym} analiz ediliyor...",
            services.scan,
            lambda r: self._analysis_done(r, focus=sym),
            settings,
            [sym],
            breadth,
            buttons=[self.analysis_button],
        )

    def _analysis_done(self, result, focus: str) -> None:
        self.analysis_result = result
        self._analysis_focus = focus
        self._set_regime(result)
        s = self._signal_for(result, focus)
        if not s:
            why = result.skipped.get(focus, "veri bulunamadı")
            self.analysis_detail.setHtml(f"<b>{focus}</b>: analiz edilemedi ({why}).")
            self.analysis_canvas.figure.clear()
            self.analysis_canvas.draw_idle()
            return
        self._show_detail(s, self.analysis_detail, self.analysis_score_canvas, result)
        self._redraw_analysis()

    def _redraw_analysis(self) -> None:
        result, sym = self.analysis_result, self._analysis_focus
        s = self._signal_for(result, sym) if result else None
        if not s or sym not in result.features:
            return
        entry = next((h.entry_price for h in self.holdings if h.symbol == sym), None)
        charts.price_chart(
            self.analysis_canvas.figure,
            result.features[sym],
            sym,
            s.risk if s.signal in report.PLAN_SIGNALS else None,
            self.analysis_sessions.value(),
            entry_price=entry,
        )
        self.analysis_canvas.draw_idle()

    # ------------------------------------------------------------ portfolio tab
    def _portfolio_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel(
            "Elinizdeki hisseleri girin; uygulama geri testteki çıkış kurallarını (ATR stop, "
            "TP1/TP2, trend bozulması, zaman stop'u, azami tutma süresi) alış tarihinizden "
            "bugüne uygular ve her biri için <b>SAT</b>, <b>KISMİ SAT</b> veya <b>TUT</b> der."
        )
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QGroupBox("Pozisyon ekle")
        fl = QHBoxLayout(form)
        self.h_symbol = QLineEdit()
        self.h_symbol.setPlaceholderText("THYAO")
        self.h_symbol.setMaximumWidth(110)
        self.h_date = QDateEdit(QDate.currentDate().addDays(-7))
        self.h_date.setCalendarPopup(True)
        self.h_date.setDisplayFormat("dd.MM.yyyy")
        self.h_price = QDoubleSpinBox()
        self.h_price.setRange(0.01, 1e7)
        self.h_price.setDecimals(2)
        self.h_price.setSuffix(" TL")
        self.h_shares = QSpinBox()
        self.h_shares.setRange(1, 10_000_000)
        self.h_shares.setValue(100)
        self.h_stop = QDoubleSpinBox()
        self.h_stop.setRange(0, 1e7)
        self.h_stop.setDecimals(2)
        self.h_stop.setSpecialValueText("otomatik (ATR)")
        self.h_tp1 = QCheckBox("TP1'de kısmi sattım")
        add = QPushButton("Ekle")
        add.clicked.connect(self.add_holding)
        for label, wid in (
            ("Sembol", self.h_symbol),
            ("Alış tarihi", self.h_date),
            ("Alış fiyatı", self.h_price),
            ("Lot", self.h_shares),
            ("Stop", self.h_stop),
        ):
            fl.addWidget(QLabel(label))
            fl.addWidget(wid)
        fl.addWidget(self.h_tp1)
        fl.addWidget(add)
        fl.addStretch()
        lay.addWidget(form)
        bar = QHBoxLayout()
        self.portfolio_button = QPushButton("▶  Kontrol et")
        self.portfolio_button.setStyleSheet("font-weight:bold;padding:6px 18px")
        self.portfolio_button.clicked.connect(self.start_portfolio)
        remove = QPushButton("Seçiliyi sil")
        remove.clicked.connect(self.remove_holding)
        chart = QPushButton("Grafikte aç")
        chart.clicked.connect(self._portfolio_open_chart)
        bar.addWidget(self.portfolio_button)
        bar.addWidget(remove)
        bar.addWidget(chart)
        bar.addStretch()
        lay.addLayout(bar)
        self.portfolio_model = RowsModel(colors=signal_colors())
        self.portfolio_view, self.portfolio_proxy = make_table(self.portfolio_model)
        lay.addWidget(self.portfolio_view, 1)
        self.portfolio_note = QLabel("")
        self.portfolio_note.setWordWrap(True)
        lay.addWidget(self.portfolio_note)
        self._fill_portfolio_plain()
        return w

    def _fill_portfolio_plain(self) -> None:
        rows = [
            {
                "Sembol": h.symbol,
                "Karar": "?",
                "Alış Tarihi": h.entry_date.strftime("%d.%m.%Y"),
                "Alış": h.entry_price,
                "Lot": h.shares,
                "Stop": h.stop,
                "TP1 satıldı": "evet" if h.tp1_taken else "hayır",
            }
            for h in self.holdings
        ]
        self.portfolio_model.set_rows(rows)
        self.portfolio_note.setText(
            "Kontrol için ▶ Kontrol et'e basın." if rows else "Henüz pozisyon eklenmedi."
        )

    def add_holding(self) -> None:
        sym = self.h_symbol.text().strip()
        if not sym:
            return
        self.holdings.append(
            Holding(
                symbol=normalize_symbol(sym),
                entry_date=self.h_date.date().toPython(),
                entry_price=self.h_price.value(),
                shares=self.h_shares.value(),
                stop=self.h_stop.value() or None,
                tp1_taken=self.h_tp1.isChecked(),
            )
        )
        save_holdings(self.home, self.holdings)
        self.h_symbol.clear()
        self.portfolio_result = None
        self._fill_portfolio_plain()

    def remove_holding(self) -> None:
        idx = self.portfolio_view.selectionModel().selectedRows()
        if not idx:
            return
        i = self.portfolio_proxy.mapToSource(idx[0]).row()
        del self.holdings[i]
        save_holdings(self.home, self.holdings)
        self.portfolio_result = None
        self._fill_portfolio_plain()

    def start_portfolio(self) -> None:
        if not self.holdings:
            return
        settings = self.settings()
        breadth = load_universe(settings.universe, None)
        self.run_task(
            "Portföy kontrol ediliyor...",
            services.check_portfolio,
            self._portfolio_done,
            settings,
            list(self.holdings),
            breadth,
            buttons=[self.portfolio_button],
        )

    def _portfolio_rows(self) -> list[dict] | None:
        if not self.portfolio_result:
            return None
        return report.portfolio_rows(
            self.holdings, self.portfolio_result.checks, self.portfolio_result.scan
        )

    def _portfolio_done(self, result) -> None:
        if len(result.checks) != len(self.holdings):
            return
        self.portfolio_result = result
        self._set_regime(result.scan)
        rows = self._portfolio_rows()
        self.portfolio_model.set_rows(rows)
        reset_sort(self.portfolio_view)
        sells = sum(1 for c in result.checks if c.action is ExitAction.SELL)
        partial = sum(1 for c in result.checks if c.action is ExitAction.TAKE_PARTIAL)
        total = sum(c.pnl_try or 0 for c in result.checks)
        self.portfolio_note.setText(
            f"{sells} SAT, {partial} KISMİ SAT, {len(rows) - sells - partial} TUT · toplam "
            f"açık K/Z {money(total, 0)} TL · Fiyatlar {result.scan.as_of:%d.%m.%Y} kapanışı. "
            "SAT kararı ertesi seansın açılışında uygulanacak şekilde hesaplanır."
        )

    def _portfolio_open_chart(self) -> None:
        row = selected_row(self.portfolio_view, self.portfolio_proxy, self.portfolio_model)
        if not row:
            return
        self.analysis_symbol.setText(row["Sembol"])
        self.tabs.setCurrentIndex(1)
        if self.portfolio_result and row["Sembol"] in self.portfolio_result.scan.features:
            self._analysis_done(self.portfolio_result.scan, focus=row["Sembol"])
        else:
            self.start_analysis()

    # ------------------------------------------------------------ backtest tab
    def _backtest_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.bt_universe = QComboBox()
        self.bt_universe.addItems(["BIST30", "BIST50", "BIST100", "Özel liste"])
        self.bt_start = QDateEdit(QDate.fromString(self.prefs.backtest_start, "yyyy-MM-dd"))
        self.bt_start.setCalendarPopup(True)
        self.bt_start.setDisplayFormat("dd.MM.yyyy")
        self.bt_end = QDateEdit(QDate.currentDate())
        self.bt_end.setCalendarPopup(True)
        self.bt_end.setDisplayFormat("dd.MM.yyyy")
        self.bt_button = QPushButton("▶  Geri testi çalıştır")
        self.bt_button.setStyleSheet("font-weight:bold;padding:6px 18px")
        self.bt_button.clicked.connect(self.start_backtest)
        self.bt_export = QPushButton("Excel'e aktar")
        self.bt_export.setEnabled(False)
        self.bt_export.clicked.connect(self.export_backtest)
        for label, wid in (
            ("Evren:", self.bt_universe),
            ("Başlangıç:", self.bt_start),
            ("Bitiş:", self.bt_end),
        ):
            bar.addWidget(QLabel(label))
            bar.addWidget(wid)
        bar.addStretch()
        bar.addWidget(self.bt_export)
        bar.addWidget(self.bt_button)
        lay.addLayout(bar)
        note = QLabel(
            "Geri test, tarayıcının aynı puan ve sinyal kurallarını geçmişe uygular: T günü "
            "kapanışta sinyal, T+1'de giriş, komisyon ve kayma dahil. Başlangıç sermayesi "
            "Ayarlar'daki portföy büyüklüğüdür; lot, işlem başı riskten hesaplanır ve tek "
            "pozisyon %20, toplam açık risk %5, sektör %25 ile sınırlıdır (en fazla 8 pozisyon). "
            "Fiyat ve lotlar o günkü işlem fiyatlarıyla gösterilir. Evren bugünkü endeks "
            "üyeleridir (hayatta kalma yanlılığı sonuçları iyimser gösterir)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#666")
        lay.addWidget(note)
        self.bt_info = QLabel("")
        self.bt_info.setWordWrap(True)
        lay.addWidget(self.bt_info)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QSplitter(Qt.Orientation.Vertical)
        self.bt_metrics_model = RowsModel()
        self.bt_metrics_view, _ = make_table(self.bt_metrics_model)
        self.bt_metrics_view.setSortingEnabled(False)
        left.addWidget(self.bt_metrics_view)
        self.bt_trades_model = RowsModel()
        self.bt_trades_view, _ = make_table(self.bt_trades_model)
        left.addWidget(self.bt_trades_view)
        split.addWidget(left)
        self.bt_canvas = Canvas(7, 6)
        split.addWidget(self.bt_canvas)
        split.setSizes([650, 700])
        lay.addWidget(split, 1)
        return w

    def start_backtest(self) -> None:
        idx = self.bt_universe.currentIndex()
        prefs_universe = services.UNIVERSES[idx]
        start = self.bt_start.date().toPython()
        end = self.bt_end.date().toPython()
        if start >= end:
            self.show_error("Başlangıç tarihi bitişten önce olmalı.")
            return
        self.prefs.backtest_start = start.isoformat()
        save_prefs(self.home, self.prefs)
        settings = self.settings()
        old = self.prefs.universe
        self.prefs.universe = prefs_universe
        symbols = self.universe(settings)
        self.prefs.universe = old
        self.run_task(
            f"Geri test: {len(symbols)} hisse, {start:%d.%m.%Y} – {end:%d.%m.%Y} "
            "(ilk çalıştırmada veri indirildiği için birkaç dakika sürebilir)...",
            services.backtest,
            self._backtest_done,
            settings,
            symbols,
            prefs_universe,
            start,
            end,
            buttons=[self.bt_button],
        )

    def _backtest_done(self, summary) -> None:
        self.backtest_result = summary
        self.bt_export.setEnabled(True)
        labels = [
            ("total_return_pct", "Toplam getiri %"),
            ("cagr_pct", "Yıllık bileşik getiri %"),
            ("max_drawdown_pct", "Azami düşüş %"),
            ("sharpe", "Sharpe"),
            ("sortino", "Sortino"),
            ("volatility_pct", "Oynaklık %"),
        ]
        trade_labels = [
            ("trades", "İşlem sayısı"),
            ("win_rate_pct", "Kazanma oranı %"),
            ("avg_winner_pct", "Ort. kazanç %"),
            ("avg_loser_pct", "Ort. kayıp %"),
            ("profit_factor", "Kâr faktörü"),
            ("expectancy_r", "Beklenti (R)"),
            ("avg_holding_days", "Ort. tutma (seans)"),
            ("net_profit_try", "Net kâr TL"),
            ("total_costs_try", "Ödenen maliyet TL"),
        ]
        names = list(summary.benchmark_metrics)
        rows = []
        for key, label in labels:
            row = {"Metrik": label, "Strateji": summary.metrics.get(key)}
            for n in names:
                row[n] = summary.benchmark_metrics[n].get(key)
            rows.append(row)
        for key, label in trade_labels:
            v = summary.metrics.get(key)
            rows.append({"Metrik": label, "Strateji": v})
        for r in rows:
            for k in list(r)[1:]:
                r.setdefault(k, None)
        cols = list(rows[0])
        rows = [{c: r.get(c) for c in cols} for r in rows]
        self.bt_metrics_model.set_rows(rows)
        reset_sort(self.bt_metrics_view)
        reason_tr = {
            "tp1": "TP1",
            "tp2": "TP2",
            "stop": "Stop",
            "breakeven_stop": "Maliyet stop",
            "trailing_stop": "İz süren stop",
            "trend_exit": "Trend bozuldu",
            "time_stop": "Zaman stop",
            "max_hold": "Azami süre",
            "end_of_data": "Dönem sonu",
        }
        trades = []
        for t in summary.trades.sort_values("entry_date", ascending=False).itertuples():
            trades.append(
                {
                    "Sembol": t.symbol,
                    "Giriş": t.entry_date.strftime("%d.%m.%Y"),
                    "Çıkış": t.exit_date.strftime("%d.%m.%Y"),
                    "Giriş Fiyatı": float(t.entry_price),
                    "Çıkış Fiyatı": float(t.exit_price),
                    "Lot": int(t.shares),
                    "Pozisyon TL": int(round(float(t.position_value))),
                    "Özkaynak %": round(float(t.equity_pct), 1),
                    "Getiri %": round(float(t.return_pct), 2),
                    "R": round(float(t.r_multiple), 2),
                    "K/Z TL": round(float(t.pnl), 0),
                    "Seans": int(t.holding_days),
                    "Çıkış Nedeni": reason_tr.get(t.exit_reason, t.exit_reason),
                    "Puan": round(float(t.score), 1),
                }
            )
        self.bt_trades_model.colors = {}
        self.bt_trades_model.set_rows(trades)
        reset_sort(self.bt_trades_view)
        charts.equity_chart(
            self.bt_canvas.figure, summary.equity, {k: v for k, v in summary.benchmarks.items()}
        )
        self.bt_canvas.draw_idle()
        self.bt_info.setText(backtest_info(summary, len(trades)))

    def export_backtest(self) -> None:
        if not self.backtest_result:
            return
        path = self._save_dialog(
            "Geri test raporu", f"geritest_{date.today():%Y%m%d}.xlsx", "Excel (*.xlsx)"
        )
        if path:
            report.export_backtest_excel(self.backtest_result, path)
            self._open_path(path)

    # ------------------------------------------------------------ settings tab
    def _settings_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        box = QGroupBox("Tarama ayarları")
        box_lay = QVBoxLayout(box)
        form = QFormLayout()
        box_lay.addLayout(form)
        box_lay.addStretch()
        self.s_provider = QComboBox()
        self.s_provider.addItem("Yahoo Finance (gerçek veri, internet gerekir)", "yahoo")
        self.s_provider.addItem("Sentetik demo verisi (çevrimdışı deneme)", "synthetic")
        self.s_provider.addItem("Kendi CSV dosyalarım (veri klasörü/data/raw)", "csv")
        self.s_provider.setCurrentIndex(max(self.s_provider.findData(self.prefs.provider), 0))
        self.s_news = QCheckBox("KAP haberlerini puana kat")
        self.s_news.setChecked(self.prefs.news)
        base = services.load_app_settings(self.home, Prefs(provider="synthetic"))
        self.s_threshold = QDoubleSpinBox()
        self.s_threshold.setRange(40, 100)
        self.s_threshold.setDecimals(0)
        self.s_threshold.setValue(
            self.prefs.buy_threshold or base.strategy.buy_threshold[MarketRegime.BULL]
        )
        thr = base.strategy.buy_threshold
        self.s_threshold_hint = QLabel(
            "Varsayılan: "
            + ", ".join(f"{REGIME_TR[k]} {v:.0f}" for k, v in thr.items())
            + ". Diğer piyasa durumlarının eşikleri aynı miktarda kayar."
        )
        self.s_threshold_hint.setWordWrap(True)
        self.s_cap = QCheckBox("Getiri hedefini üstteki dirençle sınırla")
        self.s_cap.setChecked(
            self.prefs.use_resistance_cap
            if self.prefs.use_resistance_cap is not None
            else base.risk.use_resistance_cap
        )
        self.s_rr = QDoubleSpinBox()
        self.s_rr.setRange(0.5, 5)
        self.s_rr.setSingleStep(0.1)
        self.s_rr.setValue(self.prefs.minimum_rr or base.risk.minimum_rr)
        self.s_equity = QDoubleSpinBox()
        self.s_equity.setRange(1_000, 1e10)
        self.s_equity.setDecimals(0)
        self.s_equity.setGroupSeparatorShown(True)
        self.s_equity.setSuffix(" TL")
        self.s_equity.setValue(self.prefs.portfolio_equity or base.risk.portfolio_equity)
        self.s_risk = QDoubleSpinBox()
        self.s_risk.setRange(0.1, 10)
        self.s_risk.setSingleStep(0.1)
        self.s_risk.setSuffix(" %")
        self.s_risk.setValue(self.prefs.risk_per_trade_pct or base.risk.risk_per_trade_pct)
        self.s_bear = QCheckBox("AYI piyasasında AL sinyali verme")
        self.s_bear.setChecked(
            self.prefs.block_buys_in_bear
            if self.prefs.block_buys_in_bear is not None
            else base.strategy.block_buys_in_bear
        )
        self.s_custom = QPlainTextEdit(" ".join(self.prefs.custom_symbols))
        self.s_custom.setPlaceholderText("Özel liste: THYAO ASELS TUPRS ...")
        self.s_custom.setMaximumHeight(70)
        form.addRow("Veri kaynağı", self.s_provider)
        form.addRow("", self.s_news)
        form.addRow("BOĞA piyasası AL eşiği", self.s_threshold)
        form.addRow("", self.s_threshold_hint)
        form.addRow("", self.s_cap)
        form.addRow("Asgari getiri/risk", self.s_rr)
        form.addRow("Portföy büyüklüğü", self.s_equity)
        form.addRow("İşlem başına risk", self.s_risk)
        form.addRow("", self.s_bear)
        form.addRow("Özel liste sembolleri", self.s_custom)
        self.s_auto = QCheckBox("Otomatik yenile")
        self.s_auto.setChecked(self.prefs.auto_refresh)
        self.s_minutes = QSpinBox()
        self.s_minutes.setRange(1, 60)
        self.s_minutes.setSuffix(" dakikada bir")
        self.s_minutes.setValue(int(self.prefs.refresh_minutes))
        self.s_minutes.setMaximumWidth(220)
        auto_row = QHBoxLayout()
        auto_row.addWidget(self.s_auto)
        auto_row.addWidget(self.s_minutes)
        auto_row.addStretch()
        form.addRow("Yenileme", auto_row)
        auto_hint = QLabel(
            "Seans açıkken anlık fiyatlar yenilenir; seans kapanınca (18:15 sonrası) tarama "
            "kendiliğinden yeniden yapılır ve yeni günün puan ve sinyalleri gelir."
        )
        auto_hint.setWordWrap(True)
        auto_hint.setStyleSheet("color:#666")
        form.addRow("", auto_hint)
        buttons = QHBoxLayout()
        save = QPushButton("Kaydet")
        save.setStyleSheet("font-weight:bold;padding:6px 18px")
        save.clicked.connect(self.save_settings)
        reset = QPushButton("Varsayılanlara dön")
        reset.clicked.connect(self.reset_settings)
        buttons.addWidget(save)
        buttons.addWidget(reset)
        buttons.addStretch()
        form.addRow(buttons)
        for spin in (self.s_threshold, self.s_rr, self.s_equity, self.s_risk):
            spin.setMaximumWidth(220)
        outer.addWidget(box, 2)

        adv = QGroupBox("Gelişmiş")
        al = QVBoxLayout(adv)
        al.addWidget(QLabel(f"Veri klasörü:\n{self.home}"))
        open_btn = QPushButton("Veri klasörünü aç")
        open_btn.clicked.connect(lambda: self._open_path(self.home))
        al.addWidget(open_btn)
        yaml_btn = QPushButton("YAML ayar dosyalarını düzenlemek için dışa aktar")
        yaml_btn.clicked.connect(self.export_yaml)
        al.addWidget(yaml_btn)
        hint = QLabel(
            "Dışa aktarılan dosyalar veri klasöründeki config/ altına kopyalanır. Oradaki "
            "settings.yaml, scoring.yaml, universe.yaml ve backtest.yaml dosyaları uygulamanın "
            "yerleşik ayarlarının yerine geçer (indikatör periyotları, ağırlıklar, endeks "
            "listeleri, çıkış kuralları...). Bu sayfadaki ayarlar yine de onların üzerine "
            "uygulanır."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666")
        al.addWidget(hint)
        al.addStretch()
        outer.addWidget(adv, 1)
        return w

    def save_settings(self) -> None:
        base = services.load_app_settings(self.home, Prefs(provider="synthetic"))
        p = self.prefs
        p.provider = self.s_provider.currentData()
        p.news = self.s_news.isChecked()
        thr = self.s_threshold.value()
        p.buy_threshold = None if thr == base.strategy.buy_threshold[MarketRegime.BULL] else thr
        cap = self.s_cap.isChecked()
        p.use_resistance_cap = None if cap == base.risk.use_resistance_cap else cap
        rr = round(self.s_rr.value(), 2)
        p.minimum_rr = None if rr == base.risk.minimum_rr else rr
        eq = self.s_equity.value()
        p.portfolio_equity = None if eq == base.risk.portfolio_equity else eq
        risk = round(self.s_risk.value(), 2)
        p.risk_per_trade_pct = None if risk == base.risk.risk_per_trade_pct else risk
        bear = self.s_bear.isChecked()
        p.block_buys_in_bear = None if bear == base.strategy.block_buys_in_bear else bear
        p.custom_symbols = [s for s in self.s_custom.toPlainText().replace(",", " ").split() if s]
        p.auto_refresh = self.s_auto.isChecked()
        p.refresh_minutes = self.s_minutes.value()
        try:
            self.settings()
        except Exception as exc:  # noqa: BLE001
            self.show_error(f"Ayarlar geçersiz: {exc}")
            return
        save_prefs(self.home, p)
        self._configure_auto_refresh()
        self._update_source_status()
        self.statusBar().showMessage(
            "Ayarlar kaydedildi. Yeni ayarlar bir sonraki taramada kullanılacak.", 8000
        )

    def reset_settings(self) -> None:
        provider = self.prefs.provider
        self.prefs = Prefs(
            provider=provider,
            universe=self.prefs.universe,
            custom_symbols=self.prefs.custom_symbols,
        )
        save_prefs(self.home, self.prefs)
        idx = self.tabs.currentIndex()
        self.tabs.removeTab(4)
        self.tabs.insertTab(4, self._settings_tab(), "Ayarlar")
        self.tabs.setCurrentIndex(idx)

    def export_yaml(self) -> None:
        target = paths.export_default_config(self.home)
        self._open_path(target)

    # ------------------------------------------------------------ help tab
    def _help_tab(self) -> QWidget:
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        rows = "".join(
            f"<tr><td style='background:{SIGNAL_COLOR[s]};padding:3px 8px'><b>{signal_text(s)}"
            f"</b></td><td>{desc}</td></tr>"
            for s, desc in (
                (
                    SignalType.STRONG_BUY_CANDIDATE,
                    "Puan AL eşiğinin en az 10 puan üzerinde, "
                    "getiri/risk yeterli, likidite ve veri filtreleri geçti.",
                ),
                (
                    SignalType.BUY_CANDIDATE,
                    "Puan piyasa durumuna göre AL eşiğinin üzerinde ve tüm risk filtreleri geçti.",
                ),
                (
                    SignalType.WEAK_SETUP,
                    "Puan iyi ama eşiğin altında, ya da puan yeterli olduğu "
                    "halde getiri/risk (çoğunlukla üstteki direnç nedeniyle) yetersiz.",
                ),
                (SignalType.WATCH, "İzlemeye değer; şimdilik işlem yok."),
                (SignalType.NO_TRADE, "Puan düşük, veri eski ya da likidite yetersiz."),
            )
        )
        view.setHtml(f"""
<h2>BISTWatcher nasıl çalışır?</h2>
<p>Her hisse için trend, momentum, hacim, göreceli güç, KAP haberleri, piyasa durumu ve
oynaklık faktörlerinden 0–100 arası açıklanabilir bir puan hesaplanır. Sinyal, seans
kapanışındaki veriye göre üretilir; giriş ertesi seansta, verilen giriş bölgesinde düşünülür.</p>
<h3>Sinyaller</h3><table cellspacing="4">{rows}</table>
<h3>SAT ne demek?</h3>
<p>Strateji yalnızca alım (long) tarafında çalışır; açığa satış önermez. <b>SAT</b>, elinizdeki
bir pozisyondan çıkmak demektir:</p>
<ul>
<li><b>Tarama tablosundaki "Elindeyse" sütunu</b>: kapanış EMA20'nin ve MACD sinyal çizgisinin
altındaysa (geri testteki trend çıkış kuralı) <b>SAT</b>, değilse <b>TUT</b>.</li>
<li><b>Portföyüm sekmesi</b>: alış tarihinizden bugüne ATR stop, TP1 (kısmi satış), TP2, trend
bozulması, zaman stop'u ve azami tutma süresini uygular.</li>
</ul>
<h3>Risk planı</h3>
<p>Stop = giriş − 2×ATR, TP1 = 1,5R, TP2 = 2,5R. Lot sayısı, Ayarlar'daki portföy büyüklüğü ve
işlem başına risk yüzdesine göre hesaplanır. AL için getiri/risk en az asgari değer olmalıdır;
üstte yakın bir direnç varsa hedef dirençle sınırlanır (Ayarlar'dan kapatılabilir).</p>
<h3>Veri</h3>
<p>Gerçek fiyatlar Yahoo Finance'ten (günlük, ~15 dk gecikmeli) indirilir ve veri klasöründe
önbelleğe alınır. KAP bildirimleri kap.org.tr'den alınır. İnternet yoksa Ayarlar'dan sentetik
demo verisini seçebilirsiniz.</p>
<p style="color:#888">{report.DISCLAIMER}</p>
<p style="color:#888">Sürüm {__version__} · Veri klasörü: {self.home}</p>
""")
        return view


# ---------------------------------------------------------------- entry points


def self_test(out_path: str | None) -> int:
    """Exercise the whole app offline (synthetic data); used by the Windows build CI."""
    lines: list[str] = []
    code = 0
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        os.environ["BISTWATCHER_HOME"] = tmp
        try:
            save_prefs(home, Prefs(provider="synthetic", universe="BIST30"))
            prefs = load_prefs(home)
            settings = services.load_app_settings(home, prefs)
            symbols = services.universe_symbols(settings, prefs)
            result = services.scan(settings, symbols, symbols)
            lines.append(f"scan: {len(result.signals)} signals, regime {result.regime.regime}")
            report.export_scan_excel(result, home / "r.xlsx")
            report.export_scan_html(result, home / "r.html")
            lines.append("export: ok")
            last = result.signals[0]
            holding = Holding(last.symbol, last.date - timedelta(days=30), last.close * 0.95, 100)
            pr = services.check_portfolio(settings, [holding], symbols)
            lines.append(f"portfolio: {pr.checks[0].action}")
            bt = services.backtest(
                settings, symbols[:8], "BIST30", date(2024, 1, 2), date(2025, 6, 30)
            )
            lines.append(f"backtest: {bt.metrics.get('trades')} trades")
            app = QApplication.instance() or QApplication(sys.argv)
            win = MainWindow(home)
            win._scan_done(result)
            win._analysis_done(result, focus=last.symbol)
            win.holdings = [holding]
            win._portfolio_done(pr)
            win._backtest_done(bt)
            win.show()
            app.processEvents()
            win.analysis_canvas.figure.savefig(home / "chart.png")
            win.close()
            lines.append("gui: ok")
        except Exception:  # noqa: BLE001
            lines.append(traceback.format_exc())
            code = 1
    lines.append("SELF-TEST " + ("OK" if code == 0 else "FAILED"))
    text = "\n".join(lines)
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
    print(text)
    return code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--self-test":
        return self_test(argv[1] if len(argv) > 1 else None)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.png"))))
    QLocale.setDefault(QLocale(QLocale.Language.Turkish, QLocale.Country.Turkey))
    win = MainWindow()
    win.show()
    if win.prefs.provider == "yahoo":
        QTimer.singleShot(400, win.start_scan)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
