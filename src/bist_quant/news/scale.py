"""Company-scale adjustment (spec §23): the same TRY amount matters more for a small firm."""

from __future__ import annotations

import math

from bist_quant.models.news import EventType

SCALED_EVENTS = {
    EventType.NEW_CONTRACT,
    EventType.GOVERNMENT_CONTRACT,
    EventType.EXPORT_DEAL,
    EventType.INVESTMENT,
    EventType.CAPACITY_EXPANSION,
    EventType.MERGER_ACQUISITION,
}


def scale_multiplier(
    amount_try: float,
    market_cap_try: float,
    reference_ratio: float = 0.01,
    sensitivity: float = 0.5,
    lo: float = 0.5,
    hi: float = 2.0,
) -> float:
    """``1 + sensitivity * log10(ratio / reference)``, clipped to [lo, hi].

    With the defaults an amount equal to 1% of market cap is neutral (1.0), 10% -> 1.5,
    100% -> 2.0 and 0.1% -> 0.5.
    """
    if amount_try <= 0 or market_cap_try <= 0:
        return 1.0
    ratio = amount_try / market_cap_try
    return min(max(1.0 + sensitivity * math.log10(ratio / reference_ratio), lo), hi)
