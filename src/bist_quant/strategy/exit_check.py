"""Exit check for positions the user actually holds.

The scanner only produces long-entry signals. A "sell" in this project means
exiting a held long position, so this module replays the backtest's exit rules
(:mod:`bist_quant.backtest.engine`) on one real position:

* ATR stop (``risk.atr_stop_multiplier`` x ATR at the entry session), or the
  user's own stop when given; moved to breakeven after TP1 when configured,
  optionally trailed by ATR.
* TP1 (sell ``tp1_fraction`` of the position) and TP2 (sell the rest).
* Trend exit (close < EMA20 and MACD < signal), time stop and maximum holding.

Feature frames hold back-adjusted prices; the user's entry price is as traded, so
every bar is converted to the traded price scale with ``raw_close / close``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

import pandas as pd

from bist_quant.config import Settings


class ExitAction(StrEnum):
    HOLD = "HOLD"
    TAKE_PARTIAL = "TAKE_PARTIAL"  # TP1 reached: sell tp1_fraction, keep the rest
    SELL = "SELL"


@dataclass
class Holding:
    symbol: str
    entry_date: date
    entry_price: float  # as traded, TRY
    shares: int
    stop: float | None = None  # user's own stop; None = ATR stop from the entry session
    tp1_taken: bool = False  # the user already sold the TP1 part


@dataclass
class ExitCheck:
    symbol: str
    action: ExitAction
    reasons: list[str]  # codes: stop, breakeven_stop, trailing_stop, tp1, tp2, trend_exit,
    #                     time_stop, max_hold, no_data, before_entry
    last_date: date | None = None
    last_close: float | None = None
    stop: float | None = None
    initial_stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    sessions_held: int = 0
    tp1_reached: bool = False
    pnl_pct: float | None = None
    pnl_try: float | None = None
    r_multiple: float | None = None
    events: dict[str, date] = field(default_factory=dict)  # reason code -> first date


def trend_exit_triggered(row: pd.Series | dict) -> bool:
    """The backtest's trend-failure rule: close < EMA20 and MACD below its signal."""
    try:
        c, ema, m, s = (float(row[k]) for k in ("close", "ema_fast", "macd", "macd_signal"))
    except (KeyError, TypeError, ValueError):
        return False
    if any(math.isnan(x) for x in (c, ema, m, s)):
        return False
    return c < ema and m < s


def check_holding(holding: Holding, features: pd.DataFrame, settings: Settings) -> ExitCheck:
    """Replay the configured exit rules from the entry session to the latest bar."""
    if features is None or features.empty:
        return ExitCheck(holding.symbol, ExitAction.HOLD, ["no_data"])
    ex = settings.backtest.exits
    risk = settings.risk
    f = features.sort_index()
    scale = (f["raw_close"] / f["close"]).where(f["close"] > 0, 1.0).fillna(1.0)
    entry_ts = pd.Timestamp(holding.entry_date)

    upto = f.loc[f.index <= entry_ts]
    if upto.empty:
        return ExitCheck(holding.symbol, ExitAction.HOLD, ["before_entry"])
    atr_entry = float(upto["atr"].iloc[-1] * scale.loc[upto.index[-1]])

    entry = float(holding.entry_price)
    if holding.stop is not None and 0 < holding.stop < entry:
        initial_stop = float(holding.stop)
    elif atr_entry > 0 and not math.isnan(atr_entry):
        initial_stop = entry - risk.atr_stop_multiplier * atr_entry
    else:
        initial_stop = float("nan")
    per_share = entry - initial_stop
    if not per_share > 0:
        last = f.iloc[-1]
        return ExitCheck(
            holding.symbol,
            ExitAction.HOLD,
            ["no_data"],
            last_date=f.index[-1].date(),
            last_close=float(last["raw_close"]),
        )
    tp1 = entry + risk.tp1_r * per_share
    tp2 = entry + risk.tp2_r * per_share

    stop = initial_stop
    tp1_done = holding.tp1_taken
    if tp1_done and ex.breakeven_after_tp1:
        stop = max(stop, entry)
    tp1_reached = False
    highest = entry
    held = 0
    events: dict[str, date] = {}
    closed_by: str | None = None
    pending: str | None = None

    after = f.loc[f.index > entry_ts]
    for ts, row in after.iterrows():
        k = scale.loc[ts]
        o, h, low, c = (float(row[x]) * k for x in ("open", "high", "low", "close"))
        d = ts.date()
        if pending:  # the previous close asked for an exit at this open
            closed_by = pending
            events.setdefault(pending, d)
            break
        if min(o, low) <= stop:
            closed_by = _stop_code(stop, initial_stop, entry, tp1_done)
            events.setdefault(closed_by, d)
            break
        if max(o, h) >= tp1 and not tp1_reached:
            tp1_reached = True
            events.setdefault("tp1", d)
            if not tp1_done:
                tp1_done = True  # assume the plan was followed from here on
                if ex.breakeven_after_tp1:
                    stop = max(stop, entry)
        if tp1_reached and max(o, h) >= tp2:
            closed_by = "tp2"
            events.setdefault("tp2", d)
            break
        held += 1
        highest = max(highest, c)
        atr = float(row["atr"]) * k
        if ex.trailing_stop and atr > 0:
            stop = max(stop, highest - ex.trailing_atr_multiplier * atr)
        if held >= ex.max_holding_days:
            pending = "max_hold"
        elif ex.trend_exit and trend_exit_triggered(row):
            pending = "trend_exit"
        elif (
            held >= ex.time_stop_days
            and not tp1_done
            and c < entry + ex.time_stop_min_r * per_share
        ):
            pending = "time_stop"

    last_ts = f.index[-1]
    last_close = float(f["raw_close"].iloc[-1])
    reasons: list[str] = []
    if closed_by:
        reasons.append(closed_by)
        action = ExitAction.SELL
    elif pending:
        reasons.append(pending)
        events.setdefault(pending, last_ts.date())
        action = ExitAction.SELL
    elif tp1_reached and not holding.tp1_taken:
        reasons.append("tp1")
        action = ExitAction.TAKE_PARTIAL
    else:
        action = ExitAction.HOLD

    pnl_per_share = last_close - entry
    return ExitCheck(
        symbol=holding.symbol,
        action=action,
        reasons=reasons,
        last_date=last_ts.date(),
        last_close=round(last_close, 4),
        stop=round(stop, 4),
        initial_stop=round(initial_stop, 4),
        tp1=round(tp1, 4),
        tp2=round(tp2, 4),
        sessions_held=held,
        tp1_reached=tp1_reached,
        pnl_pct=round(100 * pnl_per_share / entry, 2),
        pnl_try=round(pnl_per_share * holding.shares, 2),
        r_multiple=round(pnl_per_share / per_share, 2),
        events=events,
    )


def _stop_code(stop: float, initial: float, entry: float, tp1_done: bool) -> str:
    if stop > initial + 1e-9:
        return "breakeven_stop" if tp1_done and stop <= entry + 1e-9 else "trailing_stop"
    return "stop"
