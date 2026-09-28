from __future__ import annotations

from bist_quant.config import RiskSettings, StrategySettings
from bist_quant.models.signals import MarketRegime, SignalType
from bist_quant.risk.plan import build_risk_plan
from bist_quant.strategy.entry import classify_signal

S, R = StrategySettings(), RiskSettings()
GOOD_PLAN = build_risk_plan(100, 3, R)
CAPPED_PLAN = build_risk_plan(100, 3, R, resistance=104)


def cls(score, regime=MarketRegime.BULL, plan=GOOD_PLAN, liq=True, data=True, strategy=S):
    return classify_signal(score, regime, plan, liq, data, strategy, R)


def test_bands_in_bull():
    assert cls(30)[0] is SignalType.NO_TRADE
    assert cls(55)[0] is SignalType.WATCH
    assert cls(70)[0] is SignalType.WEAK_SETUP
    assert cls(78)[0] is SignalType.BUY_CANDIDATE
    assert cls(90)[0] is SignalType.STRONG_BUY_CANDIDATE


def test_regime_raises_threshold():
    assert cls(78, MarketRegime.NEUTRAL)[0] is SignalType.WEAK_SETUP
    assert cls(85, MarketRegime.BEAR)[0] is SignalType.WEAK_SETUP
    assert cls(89, MarketRegime.BEAR)[0] is SignalType.BUY_CANDIDATE
    assert cls(98, MarketRegime.BEAR)[0] is SignalType.STRONG_BUY_CANDIDATE


def test_bear_block_option():
    blocked = StrategySettings(block_buys_in_bear=True)
    sig, notes = cls(95, MarketRegime.BEAR, strategy=blocked)
    assert sig is SignalType.WEAK_SETUP
    assert any("BEAR" in n for n in notes)


def test_score_alone_never_buys():
    sig, notes = cls(95, plan=CAPPED_PLAN)
    assert sig is SignalType.WEAK_SETUP
    assert any("reward/risk" in n for n in notes)
    assert cls(95, plan=None)[0] is SignalType.WEAK_SETUP


def test_filters_suppress_signal():
    sig, notes = cls(95, liq=False)
    assert sig is SignalType.NO_TRADE and notes
    sig, notes = cls(95, data=False)
    assert sig is SignalType.NO_TRADE and notes
