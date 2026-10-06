"""Matplotlib figures (no Qt dependency: the UI wraps them in a canvas)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from bist_quant.models.signals import RiskPlan

UP, DOWN = "#26a69a", "#ef5350"


def _style(ax) -> None:
    ax.grid(True, alpha=0.25)
    ax.tick_params(labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def price_chart(
    fig: Figure,
    features: pd.DataFrame,
    symbol: str,
    plan: RiskPlan | None = None,
    sessions: int = 160,
    entry_price: float | None = None,
) -> None:
    """Candles + EMA20/50/200 + Bollinger, volume, RSI and MACD panels."""
    fig.clear()
    f = features.tail(sessions)
    if f.empty:
        fig.text(0.5, 0.5, "Veri yok", ha="center")
        return
    x = np.arange(len(f))
    gs = fig.add_gridspec(4, 1, height_ratios=[5, 1.2, 1.4, 1.4], hspace=0.08)
    ax = fig.add_subplot(gs[0])
    axv = fig.add_subplot(gs[1], sharex=ax)
    axr = fig.add_subplot(gs[2], sharex=ax)
    axm = fig.add_subplot(gs[3], sharex=ax)

    o, h, low, c = (f[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    up = c >= o
    colors = np.where(up, UP, DOWN)
    ax.vlines(x, low, h, color=colors, linewidth=0.8)
    ax.bar(x, np.abs(c - o), bottom=np.minimum(o, c), color=colors, width=0.7, linewidth=0)
    for col, label, color in (
        ("ema_fast", "EMA20", "#ff9800"),
        ("ema_medium", "EMA50", "#2962ff"),
        ("ema_slow", "EMA200", "#7b1fa2"),
    ):
        if col in f:
            ax.plot(x, f[col], label=label, color=color, linewidth=1)
    if "bb_upper" in f:
        ax.fill_between(
            x, f["bb_lower"], f["bb_upper"], color="#90a4ae", alpha=0.12, label="Bollinger"
        )
    last = len(f) - 1
    if plan:
        span = [max(last - 25, 0), last + 6]
        for level, label, color, style in (
            (plan.stop, "Stop", DOWN, "--"),
            (plan.tp1, "TP1", UP, "--"),
            (plan.tp2, "TP2", "#1b5e20", "--"),
        ):
            ax.hlines(level, *span, colors=color, linestyles=style, linewidth=1)
            ax.text(span[1], level, f" {label} {level:,.2f}", va="center", fontsize=7, color=color)
        ax.axhspan(
            plan.entry_zone_low, plan.entry_zone_high, xmin=0.85, color="#ffeb3b", alpha=0.35
        )
        if plan.resistance:
            ax.hlines(
                plan.resistance, span[0], span[1], colors="#795548", linestyles=":", linewidth=1
            )
            ax.text(
                span[0],
                plan.resistance,
                "direnç ",
                ha="right",
                va="center",
                fontsize=7,
                color="#795548",
            )
    if entry_price:
        ax.axhline(entry_price, color="#000", linestyle=":", linewidth=1)
        ax.text(0, entry_price, f" alış {entry_price:,.2f}", va="bottom", fontsize=7)
    ax.set_title(f"{symbol}  (düzeltilmiş fiyat)", fontsize=10, loc="left")
    ax.legend(loc="upper left", fontsize=7, ncol=4, frameon=False)
    ax.set_xlim(-1, last + 12)
    _style(ax)

    axv.bar(x, f["volume"], color=colors, width=0.7, alpha=0.7)
    if "volume_ma" in f:
        axv.plot(x, f["volume_ma"], color="#555", linewidth=0.8)
    axv.set_ylabel("Hacim", fontsize=8)
    axv.yaxis.set_major_formatter(lambda v, _: f"{v / 1e6:.0f}M")
    _style(axv)

    if "rsi" in f:
        axr.plot(x, f["rsi"], color="#6a1b9a", linewidth=1)
        axr.axhline(70, color=DOWN, linewidth=0.6, linestyle="--")
        axr.axhline(30, color=UP, linewidth=0.6, linestyle="--")
        axr.set_ylim(0, 100)
    axr.set_ylabel("RSI", fontsize=8)
    _style(axr)

    if "macd" in f:
        hist = f["macd_hist"].to_numpy(float)
        axm.bar(x, hist, color=np.where(hist >= 0, UP, DOWN), width=0.7, alpha=0.6)
        axm.plot(x, f["macd"], color="#2962ff", linewidth=1, label="MACD")
        axm.plot(x, f["macd_signal"], color="#ff9800", linewidth=1, label="Sinyal")
        axm.legend(loc="upper left", fontsize=7, frameon=False, ncol=2)
    axm.set_ylabel("MACD", fontsize=8)
    _style(axm)

    dates = f.index
    step = max(len(f) // 8, 1)
    ticks = list(range(0, len(f), step))
    axm.set_xticks(ticks)
    axm.set_xticklabels([dates[i].strftime("%d.%m.%y") for i in ticks])
    for a in (ax, axv, axr):
        a.tick_params(labelbottom=False)
    fig.subplots_adjust(left=0.07, right=0.93, top=0.95, bottom=0.06)


def score_bars(fig: Figure, labels: list[str], points: list[float], maxima: list[float]) -> None:
    fig.clear()
    ax = fig.add_subplot(111)
    y = np.arange(len(labels))
    ax.barh(y, maxima, color="#e0e0e0")
    ratios = [p / m if m else 0 for p, m in zip(points, maxima, strict=True)]
    colors = [UP if r >= 0.6 else ("#f0ad4e" if r >= 0.4 else DOWN) for r in ratios]
    ax.barh(y, points, color=colors)
    for i, (p, m) in enumerate(zip(points, maxima, strict=True)):
        ax.text(m, i, f" {p:.1f}/{m:.0f}", va="center", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, max(maxima or [1]) * 1.3)
    _style(ax)
    fig.subplots_adjust(left=0.3, right=0.97, top=0.95, bottom=0.08)


def equity_chart(fig: Figure, equity: pd.Series, benchmarks: dict[str, pd.Series]) -> None:
    fig.clear()
    ax = fig.add_subplot(211)
    axd = fig.add_subplot(212, sharex=ax)
    ax.plot(equity.index, equity / equity.iloc[0] * 100, label="Strateji", color="#2962ff", lw=1.5)
    for (name, s), color in zip(
        benchmarks.items(), ["#ff9800", "#7b1fa2", "#9e9e9e"], strict=False
    ):
        ax.plot(s.index, s / s.iloc[0] * 100, label=name, color=color, lw=1)
    ax.set_title("Özkaynak (başlangıç = 100)", fontsize=10, loc="left")
    ax.legend(fontsize=7, frameon=False)
    _style(ax)
    dd = 100 * (equity / equity.cummax() - 1)
    axd.fill_between(dd.index, dd, 0, color=DOWN, alpha=0.4)
    axd.set_title("Düşüş (drawdown) %", fontsize=9, loc="left")
    _style(axd)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.94, bottom=0.07, hspace=0.3)
