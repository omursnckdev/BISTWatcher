"""Fixed-fractional position sizing."""

from __future__ import annotations

import math


def position_size(
    equity: float,
    risk_per_trade_pct: float,
    entry: float,
    stop: float,
    lot_size: int = 1,
    max_position_pct: float | None = None,
) -> int:
    """Shares such that a stop-out loses at most ``risk_per_trade_pct`` of ``equity``.

    Rounded down to ``lot_size``; optionally capped so the position value does not
    exceed ``max_position_pct`` of equity. Example: 500,000 TRY, 1%, entry 100,
    stop 94 -> floor(5,000 / 6) = 833 shares.
    """
    risk_per_share = entry - stop
    if equity <= 0 or entry <= 0 or risk_per_share <= 0:
        return 0
    shares = equity * risk_per_trade_pct / 100 / risk_per_share
    if max_position_pct is not None:
        shares = min(shares, equity * max_position_pct / 100 / entry)
    return int(math.floor(shares / lot_size) * lot_size)
