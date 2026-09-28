"""Pre-trade filters: liquidity and data quality."""

from __future__ import annotations

import math
from collections.abc import Mapping

from bist_quant.config import LiquiditySettings
from bist_quant.models.market import DataQualityReport


def liquidity_filter(row: Mapping, cfg: LiquiditySettings) -> tuple[bool, list[str]]:
    """Pass when 20D average turnover and volume clear the configured minimums."""
    reasons: list[str] = []
    turnover = row.get("avg_turnover")
    volume = row.get("avg_volume")
    ok = True
    if turnover is None or math.isnan(turnover) or turnover < cfg.min_avg_turnover_try:
        ok = False
        shown = "n/a" if turnover is None or math.isnan(turnover) else f"{turnover / 1e6:,.1f}M"
        reasons.append(
            f"Illiquid: 20D avg turnover {shown} TRY < {cfg.min_avg_turnover_try / 1e6:,.0f}M"
        )
    if volume is None or math.isnan(volume) or volume < cfg.min_avg_volume:
        ok = False
        reasons.append(f"Illiquid: 20D avg volume below {cfg.min_avg_volume:,.0f} shares")
    return ok, reasons


def data_filter(report: DataQualityReport) -> tuple[bool, list[str]]:
    """Fail on stale / suspended data (stale-data protection)."""
    if report.is_stale:
        return False, [f"Stale data: {issue}" for issue in report.issues] or ["Stale data"]
    return True, []
