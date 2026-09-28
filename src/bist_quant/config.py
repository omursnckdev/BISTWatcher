from dataclasses import dataclass, fields
from pathlib import Path
import math
import re
import tomllib


@dataclass(frozen=True)
class Settings:
    symbols: tuple[str, ...] = ("THYAO", "ASELS", "TUPRS", "BIMAS", "KCHOL")
    benchmark: str = "XU100"
    minimum_history: int = 250
    max_age_days: int = 5
    minimum_turnover: float = 50_000_000
    equity: float = 500_000
    risk_fraction: float = 0.01
    max_position_fraction: float = 0.20
    atr_stop_multiplier: float = 2.0
    tp1_r: float = 1.5
    tp2_r: float = 2.5
    minimum_rr: float = 2.0

    def __post_init__(self):
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be nonempty and unique")
        for symbol in (*self.symbols, self.benchmark):
            if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{2,12}", symbol):
                raise ValueError("Invalid symbol")
        for field in fields(self):
            if field.name in ("symbols", "benchmark"):
                continue
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field.name} must be positive and finite")
        if type(self.minimum_history) is not int or self.minimum_history < 250:
            raise ValueError("minimum_history must be an integer >= 250")
        if type(self.max_age_days) is not int:
            raise ValueError("max_age_days must be an integer")
        if self.risk_fraction > 1 or self.max_position_fraction > 1:
            raise ValueError("Risk and position fractions must be <= 1")
        if self.tp2_r < self.minimum_rr or self.tp1_r >= self.tp2_r:
            raise ValueError("Require tp1_r < tp2_r and tp2_r >= minimum_rr")


def load_settings(path: Path | None) -> Settings:
    data = {} if path is None else tomllib.loads(path.read_text())
    if "symbols" in data:
        if not isinstance(data["symbols"], list):
            raise ValueError("symbols must be an array")
        data["symbols"] = tuple(data["symbols"])
    return Settings(**data)
