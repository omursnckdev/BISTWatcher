"""Composite 0-100 score from the individual factor scores."""

from __future__ import annotations

from collections.abc import Mapping

from bist_quant.config import Settings
from bist_quant.models.signals import ComponentScore
from bist_quant.scoring.technical_score import (
    momentum_score,
    relative_strength_score,
    trend_score,
    volatility_score,
    volume_score,
)

COMPONENT_ORDER = [
    "trend",
    "momentum",
    "volume",
    "relative_strength",
    "news",
    "institutional_flow",
    "market_regime",
    "volatility",
]


def disabled_component(name: str, weight: float, reason: str) -> ComponentScore:
    return ComponentScore(
        name=name, points=0.0, max_points=weight, enabled=False, negative_factors=[reason]
    )


def score_components(
    row: Mapping,
    settings: Settings,
    regime_component: ComponentScore,
    extra: Mapping[str, ComponentScore] | None = None,
) -> dict[str, ComponentScore]:
    """Build all eight components. ``extra`` supplies news / flow scores when available."""
    w = settings.scoring.weights
    rules = settings.scoring.rules
    ind = settings.indicators
    extra = dict(extra or {})
    comps = {
        "trend": trend_score(row, w.trend, rules, ind),
        "momentum": momentum_score(row, w.momentum, rules),
        "volume": volume_score(row, w.volume, rules),
        "relative_strength": relative_strength_score(
            row, w.relative_strength, ind, settings.market_regime.index
        ),
        "news": extra.pop("news", None)
        or disabled_component("news", w.news, "News/KAP engine not enabled (Phase 3)"),
        "institutional_flow": extra.pop("institutional_flow", None)
        or disabled_component(
            "institutional_flow", w.institutional_flow, "Institutional flow not enabled (Phase 4)"
        ),
        "market_regime": regime_component,
        "volatility": volatility_score(row, w.volatility, rules, ind),
    }
    if extra:
        raise ValueError(f"unknown extra components: {sorted(extra)}")
    return {name: comps[name] for name in COMPONENT_ORDER}


def composite_score(components: Mapping[str, ComponentScore], renormalize: bool = True) -> float:
    """Sum of component points, clipped to 0-100.

    With ``renormalize`` the total is rescaled over the *enabled* components, so a
    Phase 1 technical-only score still spans 0-100 (e.g. 56/70 enabled -> 80).
    """
    enabled = [c for c in components.values() if c.enabled]
    points = sum(c.points for c in enabled)
    if renormalize:
        available = sum(c.max_points for c in enabled)
        total = 100 * points / available if available else 0.0
    else:
        total = points
    return round(min(max(total, 0.0), 100.0), 1)
