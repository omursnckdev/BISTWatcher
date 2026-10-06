from __future__ import annotations

import pytest

from bist_quant.config import RiskSettings
from bist_quant.risk.plan import build_risk_plan
from bist_quant.risk.position_size import position_size
from bist_quant.risk.stop_loss import atr_stop, trailing_stop
from bist_quant.risk.take_profit import r_targets

CAP = RiskSettings(use_resistance_cap=True)  # the cap is off by default


def test_atr_stop_spec_example():
    assert atr_stop(100, 3, 2) == 94


def test_atr_stop_rejects_non_positive():
    with pytest.raises(ValueError):
        atr_stop(10, 6, 2)
    with pytest.raises(ValueError):
        atr_stop(10, 0, 2)


def test_r_targets():
    tp1, tp2 = r_targets(100, 94, 1.5, 2.5)
    assert tp1 == pytest.approx(109)
    assert tp2 == pytest.approx(115)
    with pytest.raises(ValueError):
        r_targets(100, 101)


def test_position_size_spec_example():
    assert position_size(500_000, 1.0, 100, 94) == 833


def test_position_size_caps_and_lots():
    assert position_size(500_000, 1.0, 100, 99.9, max_position_pct=20) == 1000
    assert position_size(500_000, 1.0, 100, 94, lot_size=100) == 800
    assert position_size(500_000, 1.0, 100, 101) == 0


def test_trailing_stop_never_moves_down():
    s1 = trailing_stop(110, 3, 2)
    assert s1 == 104
    assert trailing_stop(105, 3, 2, current_stop=s1) == s1
    assert trailing_stop(120, 3, 2, current_stop=s1) == 114


def test_risk_plan_without_resistance():
    plan = build_risk_plan(100, 3, CAP, resistance=None)
    assert plan is not None
    assert (plan.stop, plan.tp1, plan.tp2) == (94, 109, 115)
    assert plan.risk_reward == 2.5
    assert plan.shares == 833
    assert plan.capital_at_risk <= 5000
    assert plan.entry_zone_low == 99.25 and plan.entry_zone_high == 100.75


def test_risk_plan_resistance_cap():
    plan = build_risk_plan(100, 3, CAP, resistance=106)
    assert plan.resistance_capped
    assert plan.risk_reward == 1.0
    # Resistance above TP2 or below entry does not cap.
    assert not build_risk_plan(100, 3, CAP, resistance=130).resistance_capped
    assert not build_risk_plan(100, 3, CAP, resistance=99).resistance_capped
    off = RiskSettings(use_resistance_cap=False)
    assert build_risk_plan(100, 3, off, resistance=106).risk_reward == 2.5


def test_risk_plan_invalid_atr():
    assert build_risk_plan(100, float("nan"), RiskSettings()) is None
    assert build_risk_plan(100, 0, RiskSettings()) is None
    assert build_risk_plan(10, 6, RiskSettings()) is None  # stop would be negative
