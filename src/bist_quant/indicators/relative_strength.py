"""Relative strength versus a benchmark index."""

from __future__ import annotations

import pandas as pd


def align_to(series: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Align a benchmark series onto ``index`` using only past values (forward fill)."""
    return series.reindex(series.index.union(index)).ffill().reindex(index)


def relative_strength(
    close: pd.Series, benchmark_close: pd.Series, windows: list[int] | tuple[int, ...]
) -> pd.DataFrame:
    """``rs_<n>d`` = stock n-day % return minus benchmark n-day % return (percentage points)."""
    bench = align_to(benchmark_close, close.index)
    out = {}
    for n in windows:
        stock_ret = (close / close.shift(n) - 1) * 100
        bench_ret = (bench / bench.shift(n) - 1) * 100
        out[f"ret_{n}d"] = stock_ret
        out[f"rs_{n}d"] = stock_ret - bench_ret
    return pd.DataFrame(out, index=close.index)
