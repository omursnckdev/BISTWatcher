"""Shared fixtures."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bist_quant.config import Settings, load_settings


def make_bars(
    n: int = 400,
    seed: int = 0,
    drift: float = 0.0005,
    vol: float = 0.015,
    start: str = "2023-01-02",
) -> pd.DataFrame:
    """Random-walk OHLCV bars with a valid high/low envelope."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n, name="date")
    rets = drift + rng.normal(0, vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[100.0], close[:-1]]) * np.exp(rng.normal(0, vol / 4, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    volume = rng.lognormal(14, 0.4, n)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "adjusted_close": close,
        },
        index=idx,
    )


def trending_bars(n: int = 400, daily: float = 0.004, noise: float = 0.004, seed: int = 1):
    return make_bars(n=n, seed=seed, drift=daily, vol=noise)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture()
def bars() -> pd.DataFrame:
    return make_bars()
