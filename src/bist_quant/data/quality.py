"""Data cleaning, corporate-action adjustment and stale/missing data checks."""

from __future__ import annotations

from datetime import date, datetime, time

import numpy as np
import pandas as pd

from bist_quant.data.market_data import ISTANBUL_TZ
from bist_quant.models.market import OHLCV_COLUMNS, DataQualityReport


def clean_bars(frame: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, DataQualityReport]:
    """Sort, de-duplicate and drop invalid candles. Never fills or invents prices."""
    report = DataQualityReport(symbol=symbol, bars=0)
    frame = frame.sort_index()
    dup_mask = frame.index.duplicated(keep="last")
    report.dropped_duplicate_rows = int(dup_mask.sum())
    frame = frame[~dup_mask]

    prices = frame[["open", "high", "low", "close"]]
    valid = prices.notna().all(axis=1) & (prices > 0).all(axis=1)
    frame = frame[valid].copy()
    frame["volume"] = frame["volume"].fillna(0.0).clip(lower=0)
    frame["adjusted_close"] = frame["adjusted_close"].where(
        frame["adjusted_close"].notna() & (frame["adjusted_close"] > 0), frame["close"]
    )
    # Repair high/low that do not bracket open/close (bad vendor ticks).
    frame["high"] = frame[["open", "high", "low", "close"]].max(axis=1)
    frame["low"] = frame[["open", "high", "low", "close"]].min(axis=1)
    report.dropped_invalid_rows = int((~valid).sum())

    report.zero_volume_bars = int((frame["volume"] == 0).sum())
    report.bars = len(frame)
    if len(frame):
        report.first_date = frame.index[0].date()
        report.last_date = frame.index[-1].date()
    if report.dropped_invalid_rows:
        report.issues.append(f"dropped {report.dropped_invalid_rows} invalid candles")
    return frame[OHLCV_COLUMNS], report


def adjust_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the ``adjusted_close / close`` factor to OHLC (splits, bonus issues, dividends).

    The most recent bar has factor 1, so the latest prices remain tradable levels.
    The raw close is kept as ``raw_close``.
    """
    out = frame.copy()
    factor = (out["adjusted_close"] / out["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    out["raw_close"] = out["close"]
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * factor
    return out


def drop_incomplete_session(
    frame: pd.DataFrame, now: datetime | None = None, close_time: str = "18:15"
) -> pd.DataFrame:
    """Drop today's bar while the BIST session is still running (partial candle)."""
    if frame.empty:
        return frame
    now = now.astimezone(ISTANBUL_TZ) if now else datetime.now(ISTANBUL_TZ)
    hh, mm = (int(x) for x in close_time.split(":"))
    if frame.index[-1].date() == now.date() and now.time() < time(hh, mm):
        return frame.iloc[:-1]
    return frame


def check_freshness(
    report: DataQualityReport,
    frame: pd.DataFrame,
    calendar: pd.DatetimeIndex | None,
    today: date,
    max_age_days: int,
    max_missing_sessions: int,
) -> DataQualityReport:
    """Flag stale / suspended symbols against the benchmark index trading calendar."""
    if frame.empty:
        report.is_stale = True
        report.issues.append("no data")
        return report
    last = frame.index[-1]
    report.data_age_days = (today - last.date()).days
    if report.data_age_days > max_age_days:
        report.is_stale = True
        report.issues.append(f"last bar is {report.data_age_days} days old")
    if calendar is not None and len(calendar):
        window = calendar[calendar >= frame.index[0]]
        report.missing_sessions = int(len(window.difference(frame.index)))
        report.sessions_behind_index = int((calendar > last).sum())
        if report.sessions_behind_index > max_missing_sessions:
            report.is_stale = True
            report.issues.append(
                f"{report.sessions_behind_index} sessions behind index (suspended or stale)"
            )
        if report.missing_sessions:
            report.issues.append(f"{report.missing_sessions} sessions missing vs index calendar")
    return report
