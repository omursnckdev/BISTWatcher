"""Market data domain models."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, model_validator

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume", "adjusted_close"]


class DailyBar(BaseModel):
    """One daily OHLCV candle. DataFrames with the same columns are used for bulk work."""

    symbol: str
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    adjusted_close: float | None = None

    @model_validator(mode="after")
    def _check_range(self) -> DailyBar:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError(f"{self.symbol} {self.date}: OHLC values are inconsistent")
        return self


class DataQualityReport(BaseModel):
    """Result of cleaning / validating a symbol's price history."""

    symbol: str
    bars: int
    first_date: date | None = None
    last_date: date | None = None
    dropped_invalid_rows: int = 0
    dropped_duplicate_rows: int = 0
    zero_volume_bars: int = 0
    missing_sessions: int = 0  # sessions present in the index calendar but not here
    sessions_behind_index: int = 0  # how many index sessions after this symbol's last bar
    data_age_days: int | None = None  # calendar days between last bar and "today"
    is_stale: bool = False
    issues: list[str] = Field(default_factory=list)
