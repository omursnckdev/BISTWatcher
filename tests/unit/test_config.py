from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from bist_quant.config import IndicatorSettings, Settings, StrategySettings, load_settings
from bist_quant.data.universe import load_universe, normalize_symbol


def test_default_config_loads(settings):
    assert settings.indicators.rsi_period == 14
    assert settings.risk.atr_stop_multiplier == 2.0
    assert sum(settings.scoring.weights.model_dump().values()) == 100
    assert settings.data.provider in {"yahoo", "csv", "synthetic"}
    assert not settings.news.enabled and not settings.institutional_flow.enabled


def test_config_override_dir(tmp_path: Path):
    (tmp_path / "settings.yaml").write_text(yaml.safe_dump({"risk": {"minimum_rr": 3.0}}))
    (tmp_path / "universe.yaml").write_text(
        yaml.safe_dump({"mode": "CUSTOM", "symbols": ["thyao", "ASELS.IS"]})
    )
    s = load_settings(tmp_path)
    assert s.risk.minimum_rr == 3.0
    assert s.scoring.weights.trend == 20  # defaults when scoring.yaml is absent
    assert load_universe(s.universe) == ["THYAO", "ASELS"]


def test_invalid_config_is_rejected():
    with pytest.raises(ValidationError):
        IndicatorSettings(ema_fast=60, ema_medium=50)
    with pytest.raises(ValidationError):
        StrategySettings(buy_threshold={"BULL": 75})
    with pytest.raises(ValidationError):
        Settings.model_validate({"risk": {"risk_per_trade_pct": -1}})


def test_universe_modes(settings):
    b30 = load_universe(settings.universe, "BIST30")
    b50 = load_universe(settings.universe, "BIST50")
    b100 = load_universe(settings.universe, "BIST100")
    assert len(b30) == 30
    assert len(b50) == 50
    assert len(b100) >= 100
    assert b30 == b50[:30] and b50 == b100[:50]
    assert len(set(b100)) == len(b100)
    with pytest.raises(ValueError):
        load_universe(settings.universe, "NOPE")


def test_normalize_symbol():
    assert normalize_symbol(" thyao.is ") == "THYAO"
    with pytest.raises(ValueError):
        normalize_symbol("TH-YAO")
