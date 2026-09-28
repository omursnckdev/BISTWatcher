"""Signal classification: score + regime + risk/reward + filters -> signal type.

A score alone never produces a BUY. BUY / STRONG_BUY additionally require a
risk plan with reward/risk >= ``minimum_rr``, passing liquidity and data
filters, and (optionally) a non-BEAR regime.
"""

from __future__ import annotations

from bist_quant.config import RiskSettings, StrategySettings
from bist_quant.models.signals import MarketRegime, RiskPlan, SignalType


def buy_threshold(regime: MarketRegime, cfg: StrategySettings) -> float:
    return float(cfg.buy_threshold[regime])


def classify_signal(
    score: float,
    regime: MarketRegime,
    risk_plan: RiskPlan | None,
    liquidity_ok: bool,
    data_ok: bool,
    strategy: StrategySettings,
    risk: RiskSettings,
) -> tuple[SignalType, list[str]]:
    """Return the signal and the filter notes explaining any downgrade."""
    notes: list[str] = []
    if not data_ok:
        return SignalType.NO_TRADE, ["Signal suppressed: data is stale or incomplete"]
    if not liquidity_ok:
        return SignalType.NO_TRADE, ["Signal suppressed: liquidity filter failed"]

    threshold = buy_threshold(regime, strategy)
    strong_threshold = min(threshold + strategy.strong_buy_margin, 100.0)

    if score >= threshold:
        blocked = False
        if risk_plan is None:
            notes.append("Buy-level score but no valid risk plan (ATR unavailable)")
            blocked = True
        elif risk_plan.risk_reward < risk.minimum_rr:
            where = " (capped by overhead resistance)" if risk_plan.resistance_capped else ""
            notes.append(
                f"Buy-level score but reward/risk {risk_plan.risk_reward:.2f}{where} "
                f"< minimum {risk.minimum_rr:.2f}"
            )
            blocked = True
        if regime is MarketRegime.BEAR and strategy.block_buys_in_bear:
            notes.append("Buy-level score but buys are blocked in a BEAR regime")
            blocked = True
        if not blocked:
            if score >= strong_threshold:
                return SignalType.STRONG_BUY_CANDIDATE, notes
            return SignalType.BUY_CANDIDATE, notes
        return SignalType.WEAK_SETUP, notes

    if score >= strategy.weak_setup_threshold:
        if score >= min(strategy.buy_threshold.values()):
            notes.append(f"Score below the {regime.value} regime buy threshold ({threshold:.0f})")
        return SignalType.WEAK_SETUP, notes
    if score >= strategy.watch_threshold:
        return SignalType.WATCH, notes
    return SignalType.NO_TRADE, notes
