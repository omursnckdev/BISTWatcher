"""Tables and exportable reports (Excel / HTML / CSV) for the desktop app."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

import pandas as pd

from bist_quant.gui.texts import (
    ACTION_COLOR,
    ACTION_TR,
    COMPONENT_SHORT,
    COMPONENT_TR,
    EXIT_REASON_TR,
    SELL_COLOR,
    SIGNAL_COLOR,
    money,
    regime_text,
    signal_text,
    tr,
)
from bist_quant.models.signals import SignalResult, SignalType
from bist_quant.scanner import ScanResult
from bist_quant.strategy.exit_check import ExitAction, ExitCheck, Holding, trend_exit_triggered

BUY_SIGNALS = {SignalType.STRONG_BUY_CANDIDATE, SignalType.BUY_CANDIDATE}
PLAN_SIGNALS = BUY_SIGNALS | {SignalType.WEAK_SETUP}
DISCLAIMER = (
    "Bu rapor stratejinin seans kapanışındaki sınıflandırmasıdır; yatırım tavsiyesi "
    "değildir ve gelecekteki getiriyi garanti etmez."
)


def held_view(result: ScanResult, s: SignalResult) -> str:
    """What the strategy would do if you held this stock: TUT or SAT (trend exit rule)."""
    if not s.data_ok:
        return "-"
    frame = result.features.get(s.symbol)
    if frame is None or frame.empty:
        return "-"
    return "SAT" if trend_exit_triggered(frame.iloc[-1]) else "TUT"


def daily_change(result: ScanResult, symbol: str) -> float | None:
    frame = result.features.get(symbol)
    if frame is None or frame.empty:
        return None
    row = frame.iloc[-1]
    prev, close = row.get("prev_close"), row.get("close")
    if prev is None or close is None or pd.isna(prev) or not prev:
        return None
    return round(100 * (close / prev - 1), 2)


def enabled_components(result: ScanResult) -> list[str]:
    if not result.signals:
        return []
    comps = result.signals[0].components
    return [k for k in COMPONENT_SHORT if k in comps and comps[k].enabled]


def live_columns(result: ScanResult, s: SignalResult) -> dict:
    """'Anlık' columns, present only when the scan carries intraday quotes."""
    if not result.live_quotes:
        return {}
    q = result.live_quotes.get(s.symbol)
    return {
        "Anlık": q.price if q else None,
        "Anlık %": round(100 * (q.price / s.close - 1), 2) if q and s.close else None,
    }


def signal_rows(result: ScanResult) -> list[dict]:
    """One row per scanned stock, Turkish column names (used by the table and exports)."""
    keys = enabled_components(result)
    rows = []
    for i, s in enumerate(result.signals, 1):
        plan = s.risk if s.signal in PLAN_SIGNALS else None
        row = {
            "Sıra": i,
            "Sembol": s.symbol,
            "Puan": round(s.score, 1),
            "Sinyal": signal_text(s.signal),
            "Elindeyse": held_view(result, s),
            "Son Kapanış": s.close,
            "Günlük %": daily_change(result, s.symbol),
            **live_columns(result, s),
            "Giriş Alt": plan.entry_zone_low if plan else None,
            "Giriş Üst": plan.entry_zone_high if plan else None,
            "Stop": plan.stop if plan else None,
            "TP1": plan.tp1 if plan else None,
            "TP2": plan.tp2 if plan else None,
            "G/R": plan.risk_reward if plan else None,
            "Lot": plan.shares if plan else None,
            "Pozisyon TL": plan.position_value if plan else None,
            "Risk TL": plan.capital_at_risk if plan else None,
        }
        for k in keys:
            row[COMPONENT_SHORT[k]] = round(s.components[k].points, 1)
        row["Not"] = "; ".join(tr(x) for x in s.explanation.filters)
        rows.append(row)
    return rows


def price_basis(result: ScanResult) -> str:
    """Which price the table shows: signals use the last completed session close."""
    text = f"Fiyatlar ve sinyaller {result.as_of:%d.%m.%Y} kapanışına göre"
    if result.live_quotes:
        last = max(q.time for q in result.live_quotes.values())
        text += f"; 'Anlık' sütunu seans içi son fiyat ({last:%d.%m %H:%M}, Yahoo ~15 dk gecikmeli)"
    return text


def summary_lines(result: ScanResult) -> list[str]:
    r = result.regime
    counts = pd.Series([signal_text(s.signal) for s in result.signals]).value_counts()
    sells = sum(1 for s in result.signals if held_view(result, s) == "SAT")
    lines = [
        f"Tarih: {result.as_of:%d.%m.%Y}   Veri kaynağı: {result.provider}",
        price_basis(result),
        f"Piyasa durumu ({r.index}): {regime_text(r.regime)}  "
        f"(piyasa puanı {r.score.points:.1f}/{r.score.max_points:.0f})",
    ]
    if result.signals:
        lines.append(f"AL eşiği: {result.signals[0].buy_threshold:.0f} puan")
    if r.breadth_pct is not None:
        lines.append(f"Piyasa genişliği: hisselerin %{r.breadth_pct:.0f}'i EMA50 üzerinde")
    lines.append("Gerekçe: " + "; ".join(tr(x) for x in r.reasons))
    lines.append("Sinyaller: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    lines.append(f"Elindeyse SAT uyarısı (trend bozuk): {sells} hisse")
    if result.skipped:
        lines.append("Atlanan: " + "; ".join(f"{k} ({v})" for k, v in result.skipped.items()))
    if result.provider == "synthetic":
        lines.append("DİKKAT: Sentetik demo verisi, gerçek fiyatlar değildir.")
    return lines


def detail_lines(s: SignalResult) -> list[str]:
    out = [f"{s.symbol}  {s.date:%d.%m.%Y}  Puan {s.score:.1f}/100  {signal_text(s.signal)}"]
    for key, comp in s.components.items():
        label = COMPONENT_TR.get(key, key)
        if comp.enabled:
            out.append(f"  {label}: {comp.points:.1f}/{comp.max_points:.0f}")
        else:
            out.append(f"  {label}: kapalı")
    out += [f"  + {tr(x)}" for x in s.explanation.positive_factors]
    out += [f"  - {tr(x)}" for x in s.explanation.negative_factors]
    out += [f"  · {tr(x)}" for x in s.explanation.notes]
    out += [f"  ! {tr(x)}" for x in s.explanation.filters]
    return out


def exit_reason_text(c: ExitCheck) -> str:
    if not c.reasons:
        return "Çıkış kuralı tetiklenmedi"
    parts = []
    for r in c.reasons:
        text = EXIT_REASON_TR.get(r, r)
        when = c.events.get(r)
        parts.append(f"{text} ({when:%d.%m.%Y})" if when else text)
    return "; ".join(parts)


def portfolio_rows(holdings: list[Holding], checks: list[ExitCheck], result: ScanResult | None):
    by_symbol = {s.symbol: s for s in result.signals} if result else {}
    rows = []
    for h, c in zip(holdings, checks, strict=True):
        sig = by_symbol.get(c.symbol)
        rows.append(
            {
                "Sembol": c.symbol,
                "Karar": ACTION_TR[c.action],
                "Neden": exit_reason_text(c),
                "Alış Tarihi": h.entry_date.strftime("%d.%m.%Y"),
                "Alış": h.entry_price,
                "Lot": h.shares,
                "Son Kapanış": c.last_close,
                **(
                    {"Anlık": q.price if (q := result.live_quotes.get(c.symbol)) else None}
                    if result and result.live_quotes
                    else {}
                ),
                "K/Z %": c.pnl_pct,
                "K/Z TL": c.pnl_try,
                "R": c.r_multiple,
                "Stop": c.stop,
                "TP1": c.tp1,
                "TP2": c.tp2,
                "Seans": c.sessions_held,
                "Güncel Puan": round(sig.score, 1) if sig else None,
                "Güncel Sinyal": signal_text(sig.signal) if sig else "-",
            }
        )
    return rows


# ------------------------------------------------------------------ exports


def export_scan_excel(result: ScanResult, path: Path, portfolio: list[dict] | None = None) -> Path:
    rows = signal_rows(result)
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame({"Özet": summary_lines(result) + ["", DISCLAIMER]}).to_excel(
            xw, sheet_name="Özet", index=False
        )
        buys = df[df["Sinyal"].isin([signal_text(s) for s in BUY_SIGNALS])] if len(df) else df
        buys.to_excel(xw, sheet_name="AL Listesi", index=False)
        sells = df[df["Elindeyse"] == "SAT"] if len(df) else df
        sells.to_excel(xw, sheet_name="SAT Uyarıları", index=False)
        df.to_excel(xw, sheet_name="Tüm Hisseler", index=False)
        if portfolio:
            pd.DataFrame(portfolio).to_excel(xw, sheet_name="Portföyüm", index=False)
        details = [line for s in result.signals for line in [*detail_lines(s), ""]]
        pd.DataFrame({"Gerekçeler": details}).to_excel(xw, sheet_name="Gerekçeler", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                width = max(len(str(c.value or "")) for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 80)
            ws.freeze_panes = "A2"
    return path


def export_scan_csv(result: ScanResult, path: Path) -> Path:
    # utf-8-sig + ';' so Excel with Turkish locale opens it correctly
    pd.DataFrame(signal_rows(result)).to_csv(
        path, index=False, sep=";", encoding="utf-8-sig", decimal=","
    )
    return path


def _cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    if isinstance(v, float):
        return money(v)
    return html.escape(str(v))


def _table(rows: list[dict], color_cols: dict[str, dict[str, str]] | None = None) -> str:
    if not rows:
        return "<p class='muted'>Kayıt yok.</p>"
    cols = list(rows[0])
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = []
    for r in rows:
        tds = []
        for c in cols:
            style = ""
            if color_cols and c in color_cols and str(r[c]) in color_cols[c]:
                style = f" style='background:{color_cols[c][str(r[c])]};color:#000'"
            tds.append(f"<td{style}>{_cell(r[c])}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def export_scan_html(result: ScanResult, path: Path, portfolio: list[dict] | None = None) -> Path:
    rows = signal_rows(result)
    sig_colors = {signal_text(k): v for k, v in SIGNAL_COLOR.items()}
    held_colors = {"SAT": SELL_COLOR, "TUT": "#cfe8cf"}
    colors = {"Sinyal": sig_colors, "Elindeyse": held_colors}
    buys = [r for r in rows if r["Sinyal"] in {signal_text(s) for s in BUY_SIGNALS}]
    sells = [r for r in rows if r["Elindeyse"] == "SAT"]
    sections = [
        "<h2>Özet</h2><ul>"
        + "".join(f"<li>{html.escape(x)}</li>" for x in summary_lines(result))
        + "</ul>",
    ]
    if portfolio is not None:
        act_colors = {ACTION_TR[a]: ACTION_COLOR[a] for a in ExitAction}
        sections.append("<h2>Portföyüm</h2>" + _table(portfolio, {"Karar": act_colors}))
    sections += [
        f"<h2>AL Adayları ({len(buys)})</h2>" + _table(buys, colors),
        f"<h2>Elindeyse SAT Uyarısı ({len(sells)})</h2>"
        "<p class='muted'>Kapanış EMA20 altında ve MACD sinyal çizgisinin altında "
        "(geri testteki trend çıkış kuralı).</p>" + _table(sells, colors),
        f"<h2>Tüm Hisseler ({len(rows)})</h2>" + _table(rows, colors),
        "<h2>Gerekçeler</h2>"
        + "".join(
            "<details><summary>"
            + html.escape(detail_lines(s)[0])
            + "</summary><pre>"
            + html.escape("\n".join(detail_lines(s)[1:]))
            + "</pre></details>"
            for s in result.signals
        ),
    ]
    doc = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>BISTWatcher Raporu {result.as_of:%d.%m.%Y}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}}
h1{{margin-bottom:0}} h2{{margin-top:28px;border-bottom:2px solid #ddd}}
table{{border-collapse:collapse;font-size:12px;margin:8px 0}}
th,td{{border:1px solid #ccc;padding:3px 6px;text-align:right;white-space:nowrap}}
th{{background:#f2f2f2}} td:nth-child(2){{text-align:left;font-weight:600}}
.muted{{color:#777}} pre{{background:#fafafa;padding:8px}}
</style></head><body>
<h1>BISTWatcher Tarama Raporu</h1>
<p class="muted">Oluşturulma: {datetime.now():%d.%m.%Y %H:%M}</p>
{"".join(sections)}
<p class="muted">{DISCLAIMER}</p>
</body></html>"""
    path.write_text(doc, encoding="utf-8")
    return path


def export_backtest_excel(summary, path: Path) -> Path:
    from bist_quant.gui.services import BacktestSummary

    assert isinstance(summary, BacktestSummary)
    metrics = pd.DataFrame({"Strateji": summary.metrics, **summary.benchmark_metrics})
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        metrics.to_excel(xw, sheet_name="Metrikler")
        summary.yearly.to_excel(xw, sheet_name="Yıllık Getiri")
        summary.trades.to_excel(xw, sheet_name="İşlemler", index=False)
        pd.DataFrame({"Strateji": summary.equity, **summary.benchmarks}).to_excel(
            xw, sheet_name="Özkaynak"
        )
        if len(summary.exit_reasons):
            summary.exit_reasons.to_excel(xw, sheet_name="Çıkış Nedenleri")
    return path
