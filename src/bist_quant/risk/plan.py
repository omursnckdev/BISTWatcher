"""Combine stop, targets, reward/risk and size into a :class:`RiskPlan`."""

from __future__ import annotations

import math

from bist_quant.config import RiskSettings
from bist_quant.models.signals import RiskPlan
from bist_quant.risk.position_size import position_size
from bist_quant.risk.stop_loss import atr_stop
from bist_quant.risk.take_profit import r_targets


def build_risk_plan(
    entry: float, atr: float, cfg: RiskSettings, resistance: float | None = None
) -> RiskPlan | None:
    """Risk plan for a long entry at ``entry`` (signal on T close; execution at T+1 open
    inside the entry zone). Returns ``None`` when ATR is unusable.

    Reward/risk uses TP2 as the reward target, capped at overhead ``resistance`` when
    ``use_resistance_cap`` is set and resistance lies between entry and TP2.
    """
    if not (entry > 0 and atr and atr > 0) or math.isnan(atr):
        return None
    try:
        stop = atr_stop(entry, atr, cfg.atr_stop_multiplier)
    except ValueError:
        return None
    tp1, tp2 = r_targets(entry, stop, cfg.tp1_r, cfg.tp2_r)
    risk_per_share = entry - stop
    target, capped = tp2, False
    if (
        cfg.use_resistance_cap
        and resistance is not None
        and not math.isnan(resistance)
        and entry < resistance < tp2
    ):
        target, capped = resistance, True
    shares = position_size(
        cfg.portfolio_equity,
        cfg.risk_per_trade_pct,
        entry,
        stop,
        cfg.lot_size,
        cfg.max_position_pct,
    )
    return RiskPlan(
        entry=round(entry, 4),
        entry_zone_low=round(entry - cfg.entry_zone_atr * atr, 4),
        entry_zone_high=round(entry + cfg.entry_zone_atr * atr, 4),
        stop=round(stop, 4),
        tp1=round(tp1, 4),
        tp2=round(tp2, 4),
        risk_per_share=round(risk_per_share, 4),
        reward_target=round(target, 4),
        risk_reward=round((target - entry) / risk_per_share, 2),
        resistance=None if resistance is None or math.isnan(resistance) else round(resistance, 4),
        resistance_capped=capped,
        shares=shares,
        position_value=round(shares * entry, 2),
        capital_at_risk=round(shares * risk_per_share, 2),
    )
