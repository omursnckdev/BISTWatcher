"""Time decay of news impact (spec §22)."""

from __future__ import annotations

import math

from bist_quant.models.news import ImpactHorizon


def exponential_decay(age_hours: float, half_life_hours: float, max_age_hours: float) -> float:
    """``exp(-lambda * age)`` with ``lambda = ln 2 / half_life``; 0 beyond ``max_age``."""
    if age_hours < 0 or age_hours > max_age_hours:
        return 0.0
    return math.exp(-math.log(2) / half_life_hours * age_hours)


def step_decay(age_hours: float, table: list[tuple[float, float]]) -> float:
    """Spec §22 table, e.g. [(6, 1.0), (24, 0.7), (72, 0.4), (168, 0.15)]: the weight of
    the first bucket whose upper bound (hours) is >= age; 0 beyond the last bucket."""
    if age_hours < 0:
        return 0.0
    for upper, weight in table:
        if age_hours <= upper:
            return weight
    return 0.0


def decay_weight(age_hours: float, horizon: ImpactHorizon, cfg) -> float:
    """Decay using ``NewsDecaySettings`` (exponential per impact horizon, or step table)."""
    max_age = cfg.max_age_days * 24
    if age_hours > max_age:
        return 0.0
    if cfg.mode == "step":
        return step_decay(age_hours, [tuple(x) for x in cfg.step_table])
    return exponential_decay(age_hours, cfg.half_life_hours[horizon.value], max_age)
