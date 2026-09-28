"""Single source of truth for turning one feature row into a signal.

Used by both the live scanner and the backtester, so the backtest trades exactly
the rules the scanner reports.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from bist_quant.config import Settings
from bist_quant.models.signals import ComponentScore, MarketRegime, RiskPlan, SignalType
from bist_quant.risk.plan import build_risk_plan
from bist_quant.scoring.composite_score import composite_score, score_components
from bist_quant.strategy.entry import buy_threshold, classify_signal
from bist_quant.strategy.filters import liquidity_filter


@dataclass
class RowDecision:
    score: float
    signal: SignalType
    plan: RiskPlan | None
    liquidity_ok: bool
    components: dict[str, ComponentScore]
    liquidity_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _num(row: Mapping, key: str) -> float:
    value = row.get(key)
    return float("nan") if value is None else float(value)


def plan_for_row(row: Mapping, settings: Settings) -> RiskPlan | None:
    """Risk plan for a long entry at the row's close."""
    resistance = _num(row, "resistance")
    return build_risk_plan(
        _num(row, "close"),
        _num(row, "atr"),
        settings.risk,
        None if math.isnan(resistance) else resistance,
    )


def score_row(
    row: Mapping,
    regime_component: ComponentScore,
    settings: Settings,
    extra: Mapping[str, ComponentScore] | None = None,
) -> tuple[float, dict[str, ComponentScore]]:
    comps = score_components(row, settings, regime_component, extra)
    return composite_score(comps, settings.scoring.renormalize_missing), comps


def decide_row(
    row: Mapping,
    score: float,
    components: dict[str, ComponentScore],
    regime: MarketRegime,
    settings: Settings,
    data_ok: bool = True,
    always_plan: bool = True,
) -> RowDecision:
    """Apply risk plan, liquidity and data filters and classify the signal.

    With ``always_plan=False`` the risk plan is only built when the score reaches
    the buy threshold (the only case where it affects the signal) - used by the
    backtester to score hundreds of thousands of rows quickly.
    """
    needs_plan = always_plan or score >= buy_threshold(regime, settings.strategy)
    plan = plan_for_row(row, settings) if needs_plan else None
    liquidity_ok, liq_notes = liquidity_filter(row, settings.liquidity)
    signal, notes = classify_signal(
        score, regime, plan, liquidity_ok, data_ok, settings.strategy, settings.risk
    )
    return RowDecision(score, signal, plan, liquidity_ok, components, liq_notes, notes)
