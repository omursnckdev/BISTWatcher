"""R-multiple profit targets."""

from __future__ import annotations


def r_targets(
    entry: float, stop: float, tp1_r: float = 1.5, tp2_r: float = 2.5
) -> tuple[float, float]:
    """``TPn = entry + n * R`` where ``R = entry - stop``."""
    risk = entry - stop
    if risk <= 0:
        raise ValueError("stop must be below entry for a long position")
    return entry + tp1_r * risk, entry + tp2_r * risk
