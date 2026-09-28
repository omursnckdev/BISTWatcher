"""Signal, score and regime domain models."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class MarketRegime(StrEnum):
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class SignalType(StrEnum):
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"
    WEAK_SETUP = "WEAK_SETUP"
    BUY_CANDIDATE = "BUY_CANDIDATE"
    STRONG_BUY_CANDIDATE = "STRONG_BUY_CANDIDATE"


class ComponentScore(BaseModel):
    """Score of a single factor, already scaled to its configured weight."""

    name: str
    points: float = Field(ge=0)
    max_points: float = Field(ge=0)
    enabled: bool = True
    positive_factors: list[str] = Field(default_factory=list)
    negative_factors: list[str] = Field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.points / self.max_points if self.max_points else 0.0


class RegimeSnapshot(BaseModel):
    """Market regime on a given date, derived from the benchmark index."""

    index: str
    date: date
    regime: MarketRegime
    score: ComponentScore
    close: float
    ema50: float
    ema200: float
    atr_pct_percentile: float | None = None
    drawdown_pct: float | None = None
    breadth_pct: float | None = None
    reasons: list[str] = Field(default_factory=list)


class RiskPlan(BaseModel):
    """Entry / exit levels and position size. Mandatory for any trade entry."""

    entry: float
    entry_zone_low: float
    entry_zone_high: float
    stop: float
    tp1: float
    tp2: float
    risk_per_share: float
    reward_target: float
    risk_reward: float
    resistance: float | None = None
    resistance_capped: bool = False
    shares: int
    position_value: float
    capital_at_risk: float


class Explanation(BaseModel):
    positive_factors: list[str] = Field(default_factory=list)
    negative_factors: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)


class SignalResult(BaseModel):
    """Full, explainable output for one symbol on one date."""

    symbol: str
    date: date
    score: float = Field(ge=0, le=100)
    signal: SignalType
    market_regime: MarketRegime
    buy_threshold: float
    components: dict[str, ComponentScore]
    risk: RiskPlan | None = None
    liquidity_ok: bool
    data_ok: bool
    explanation: Explanation
    close: float
    indicators: dict[str, float | None] = Field(default_factory=dict)

    def component_points(self) -> dict[str, float | None]:
        return {
            name: (round(c.points, 2) if c.enabled else None) for name, c in self.components.items()
        }
