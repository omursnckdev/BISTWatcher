"""Configuration loading.

All tunable parameters live in ``config/*.yaml`` and are validated into typed
Pydantic models here. Secrets are read from environment variables (optionally
seeded from a ``.env`` file) and are never stored in YAML.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from bist_quant.models.signals import MarketRegime

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


class DataSettings(BaseModel):
    provider: Literal["yahoo", "csv", "synthetic"] = "yahoo"
    history_days: int = Field(800, ge=60)
    raw_dir: Path = Path("data/raw")
    cache_ttl_hours: float = Field(6.0, ge=0)
    use_adjusted_prices: bool = True
    exclude_incomplete_session: bool = True
    session_close_time: str = "18:15"
    max_data_age_days: int = Field(5, ge=0)
    max_missing_sessions: int = Field(2, ge=0)
    min_history_bars: int = Field(250, ge=30)
    request_concurrency: int = Field(4, ge=1)
    request_timeout_seconds: float = Field(20.0, gt=0)
    max_retries: int = Field(4, ge=0)

    @field_validator("session_close_time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        hh, mm = v.split(":")
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError(f"invalid HH:MM time: {v}")
        return v


class IndicatorSettings(BaseModel):
    ema_fast: int = Field(20, ge=2)
    ema_medium: int = Field(50, ge=2)
    ema_long: int = Field(100, ge=2)
    ema_slow: int = Field(200, ge=2)
    rsi_period: int = Field(14, ge=2)
    macd_fast: int = Field(12, ge=2)
    macd_slow: int = Field(26, ge=2)
    macd_signal: int = Field(9, ge=2)
    bb_period: int = Field(20, ge=2)
    bb_std: float = Field(2.0, gt=0)
    atr_period: int = Field(14, ge=2)
    adx_period: int = Field(14, ge=2)
    volume_ma_period: int = Field(20, ge=2)
    roc_period: int = Field(10, ge=1)
    rs_windows: list[int] = Field(default_factory=lambda: [5, 20, 60])

    @model_validator(mode="after")
    def _check_order(self) -> IndicatorSettings:
        if not self.ema_fast < self.ema_medium < self.ema_slow:
            raise ValueError("expected ema_fast < ema_medium < ema_slow")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("expected macd_fast < macd_slow")
        if len(self.rs_windows) != 3:
            raise ValueError("rs_windows must list exactly three windows (short, medium, long)")
        return self


class MarketRegimeSettings(BaseModel):
    index: str = "XU100"
    secondary_index: str | None = "XU030"
    high_vol_lookback: int = Field(252, ge=20)
    high_vol_percentile: float = Field(0.90, gt=0, lt=1)
    high_vol_min_ratio: float = Field(1.25, ge=1)
    drawdown_lookback: int = Field(60, ge=5)
    high_vol_drawdown_pct: float = Field(15.0, gt=0)
    breadth_healthy_pct: float = Field(50.0, ge=0, le=100)
    min_breadth_symbols: int = Field(10, ge=1)


class StrategySettings(BaseModel):
    profile: Literal["SWING"] = "SWING"
    buy_threshold: dict[MarketRegime, float] = Field(
        default_factory=lambda: {
            MarketRegime.BULL: 75,
            MarketRegime.NEUTRAL: 80,
            MarketRegime.HIGH_VOLATILITY: 85,
            MarketRegime.BEAR: 88,
        }
    )
    strong_buy_margin: float = Field(10, ge=0)
    weak_setup_threshold: float = 65
    watch_threshold: float = 50
    block_buys_in_bear: bool = False

    @model_validator(mode="after")
    def _check_thresholds(self) -> StrategySettings:
        missing = set(MarketRegime) - set(self.buy_threshold)
        if missing:
            raise ValueError(f"buy_threshold missing regimes: {sorted(m.value for m in missing)}")
        lowest_buy = min(self.buy_threshold.values())
        if not self.watch_threshold <= self.weak_setup_threshold <= lowest_buy:
            raise ValueError("expected watch <= weak_setup <= every buy threshold")
        return self


class RiskSettings(BaseModel):
    atr_stop_multiplier: float = Field(2.0, gt=0)
    tp1_r: float = Field(1.5, gt=0)
    tp2_r: float = Field(2.5, gt=0)
    minimum_rr: float = Field(2.0, gt=0)
    entry_zone_atr: float = Field(0.25, ge=0)
    use_resistance_cap: bool = True
    resistance_lookback: int = Field(120, ge=5)
    portfolio_equity: float = Field(500_000, gt=0)
    risk_per_trade_pct: float = Field(1.0, gt=0, le=100)
    max_position_pct: float = Field(20.0, gt=0, le=100)
    lot_size: int = Field(1, ge=1)


class LiquiditySettings(BaseModel):
    min_avg_turnover_try: float = Field(50_000_000, ge=0)
    min_avg_volume: float = Field(0, ge=0)


class ToggleSettings(BaseModel):
    enabled: bool = False


class FlowSettings(ToggleSettings):
    lookbacks: list[int] = Field(default_factory=lambda: [1, 3, 5, 10])


class Weights(BaseModel):
    trend: float = Field(20, ge=0)
    momentum: float = Field(15, ge=0)
    volume: float = Field(10, ge=0)
    relative_strength: float = Field(10, ge=0)
    news: float = Field(15, ge=0)
    institutional_flow: float = Field(15, ge=0)
    market_regime: float = Field(10, ge=0)
    volatility: float = Field(5, ge=0)

    @model_validator(mode="after")
    def _check_total(self) -> Weights:
        total = sum(self.model_dump().values())
        if abs(total - 100) > 1e-6:
            raise ValueError(f"scoring weights must sum to 100, got {total}")
        return self


class ScoringRules(BaseModel):
    adx_strong: float = 25
    adx_emerging: float = 20
    trend_persistence_window: int = Field(20, ge=2)
    rsi_healthy: tuple[float, float] = (45, 65)
    rsi_strong: tuple[float, float] = (65, 70)
    rsi_overbought: float = 70
    rsi_oversold: float = 30
    momentum_persistence_window: int = Field(10, ge=2)
    volume_ratio_levels: tuple[float, float, float] = (1.2, 1.5, 2.0)
    distribution_volume_ratio: float = 1.5
    obv_ema_period: int = Field(20, ge=2)
    ad_slope_window: int = Field(10, ge=2)
    atr_pct_band: tuple[float, float] = (1.5, 5.0)
    max_extension_atr: float = Field(2.0, gt=0)
    squeeze_lookback: int = Field(120, ge=20)
    squeeze_percentile: float = Field(0.20, gt=0, lt=1)


class ScoringSettings(BaseModel):
    weights: Weights = Field(default_factory=Weights)
    renormalize_missing: bool = True
    bands: dict[str, tuple[float, float]] = Field(default_factory=dict)
    rules: ScoringRules = Field(default_factory=ScoringRules)


class UniverseSettings(BaseModel):
    mode: str = "BIST30"
    symbols: list[str] = Field(default_factory=list)
    lists: dict[str, list[str]] = Field(default_factory=dict)
    sectors: dict[str, str] = Field(default_factory=dict)


class Settings(BaseModel):
    """Root configuration object."""

    data: DataSettings = Field(default_factory=DataSettings)
    indicators: IndicatorSettings = Field(default_factory=IndicatorSettings)
    market_regime: MarketRegimeSettings = Field(default_factory=MarketRegimeSettings)
    strategy: StrategySettings = Field(default_factory=StrategySettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    liquidity: LiquiditySettings = Field(default_factory=LiquiditySettings)
    news: ToggleSettings = Field(default_factory=ToggleSettings)
    institutional_flow: FlowSettings = Field(default_factory=FlowSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    universe: UniverseSettings = Field(default_factory=UniverseSettings)
    project_root: Path = PROJECT_ROOT

    def resolve_path(self, path: Path) -> Path:
        """Resolve a configured relative path against the project root."""
        return path if path.is_absolute() else self.project_root / path


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        content = yaml.safe_load(fh) or {}
    if not isinstance(content, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return content


def load_dotenv(path: Path) -> None:
    """Minimal ``.env`` reader: sets variables that are not already in the environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings(config_dir: Path | str | None = None) -> Settings:
    """Load ``settings.yaml``, ``scoring.yaml`` and ``universe.yaml`` from ``config_dir``."""
    config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    load_dotenv(PROJECT_ROOT / ".env")

    raw = _read_yaml(config_dir / "settings.yaml")
    raw["scoring"] = _read_yaml(config_dir / "scoring.yaml")
    raw["universe"] = _read_yaml(config_dir / "universe.yaml")
    return Settings.model_validate(raw)


def get_secret(name: str) -> str | None:
    """Read a credential from the environment. Returns ``None`` when unset or empty."""
    value = os.environ.get(name, "").strip()
    return value or None
