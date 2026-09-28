"""Stop-loss rules."""

from __future__ import annotations


def atr_stop(entry: float, atr: float, multiplier: float = 2.0) -> float:
    """``stop = entry - ATR * multiplier``. Raises if the stop would be <= 0."""
    if entry <= 0 or atr <= 0 or multiplier <= 0:
        raise ValueError("entry, atr and multiplier must be positive")
    stop = entry - atr * multiplier
    if stop <= 0:
        raise ValueError(f"ATR stop {stop:.4f} is not positive (entry={entry}, atr={atr})")
    return stop


def trailing_stop(
    highest_close_since_entry: float,
    atr: float,
    multiplier: float = 2.0,
    current_stop: float | None = None,
) -> float:
    """ATR trailing stop; never moves down."""
    candidate = highest_close_since_entry - atr * multiplier
    return candidate if current_stop is None else max(candidate, current_stop)
