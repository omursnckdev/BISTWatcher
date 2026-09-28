from pathlib import Path
from typing import Protocol
import hashlib
import numpy as np
import pandas as pd

REQUIRED = ["open", "high", "low", "close", "volume"]


class MarketDataProvider(Protocol):
    def daily_bars(self, symbol: str, as_of: pd.Timestamp) -> pd.DataFrame: ...


def validate_bars(frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    if not {"date", *REQUIRED}.issubset(frame.columns):
        raise ValueError("Required columns: date, open, high, low, close, volume")
    df = frame.copy()
    df["date"] = pd.to_datetime(df["date"], errors="raise")
    if df["date"].isna().any() or df["date"].dt.tz is not None:
        raise ValueError("Dates must be valid timezone-free session dates")
    if (df["date"] != df["date"].dt.normalize()).any():
        raise ValueError("Daily bars require session dates without intraday times")
    df = df.loc[df.date <= as_of].sort_values("date")
    if df.empty or df.date.duplicated().any():
        raise ValueError("Empty history or duplicate session dates")
    df[REQUIRED] = df[REQUIRED].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(df[REQUIRED].to_numpy()).all():
        raise ValueError("OHLCV must be finite")
    if (df[["open", "high", "low", "close"]] <= 0).any().any() or (df.volume < 0).any():
        raise ValueError("Prices must be positive and volume nonnegative")
    if (df.high < df[["open", "close", "low"]].max(axis=1)).any() or (df.low > df[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Invalid OHLC envelope")
    return df.set_index("date")[REQUIRED]


class CsvProvider:
    """One SYMBOL.csv per symbol. No silent price adjustments or forward filling."""
    def __init__(self, directory: Path):
        self.directory = directory

    def daily_bars(self, symbol: str, as_of: pd.Timestamp) -> pd.DataFrame:
        return validate_bars(pd.read_csv(self.directory / f"{symbol}.csv"), as_of)


class DemoProvider:
    """Deterministic synthetic data; never a real market quote."""
    def daily_bars(self, symbol: str, as_of: pd.Timestamp) -> pd.DataFrame:
        rng = np.random.default_rng(int.from_bytes(hashlib.sha256(symbol.encode()).digest()[:4]))
        dates = pd.bdate_range("2020-01-01", as_of)
        n = len(dates)
        close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, n)))
        opening = np.r_[100, close[:-1]] * np.exp(rng.normal(0, 0.002, n))
        frame = pd.DataFrame({"date": dates, "open": opening, "close": close,
                              "high": np.maximum(opening, close) * 1.008,
                              "low": np.minimum(opening, close) * 0.992,
                              "volume": rng.integers(1_000_000, 5_000_000, n)})
        return validate_bars(frame, as_of)
