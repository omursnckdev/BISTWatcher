"""News vs price reaction (spec §24).

For each event we find the first session whose close comes after publication (the
*event session*), take the close of the preceding session as the reference, and
measure the stock's excess return over the benchmark through the end of the reaction
window, in units of the stock's ATR%. The result becomes observable only at that
session's close (``reaction_known_at``) - scoring never uses it earlier.
"""

from __future__ import annotations

from datetime import datetime, time

import numpy as np
import pandas as pd

from bist_quant.data.market_data import ISTANBUL_TZ

POS, NEG = "POSITIVE", "NEGATIVE"


def session_close(day: pd.Timestamp, close_time: time) -> datetime:
    return datetime.combine(day.date(), close_time, ISTANBUL_TZ)


class ReactionCalculator:
    def __init__(
        self, bars: pd.DataFrame, benchmark_close: pd.Series, cfg, close_time: time = time(18, 0)
    ) -> None:
        self.dates = bars.index
        self.close = bars["close"].to_numpy(float)
        bench = benchmark_close.reindex(benchmark_close.index.union(bars.index)).ffill()
        self.bench = bench.reindex(bars.index).to_numpy(float)
        atr = bars.get("atr")
        self.atr_pct = (
            (atr / bars["close"] * 100).to_numpy(float)
            if atr is not None
            else np.full(len(bars), np.nan)
        )
        self.cfg = cfg
        self.close_time = close_time
        self._closes = np.array([session_close(d, close_time).timestamp() for d in self.dates])

    def event_session_index(self, published: datetime) -> int | None:
        i = int(np.searchsorted(self._closes, published.timestamp(), side="right"))
        return i if i < len(self.dates) else None

    def evaluate(
        self, published: datetime, sentiment: float
    ) -> tuple[str | None, float, datetime | None, float | None]:
        """Returns (label, multiplier, known_at, implied_sentiment)."""
        cfg = self.cfg
        i = self.event_session_index(published)
        if i is None or i == 0:
            return None, 1.0, None, None
        j = i + cfg.window_sessions - 1
        if j >= len(self.dates):
            return None, 1.0, None, None
        ref = i - 1
        stock = self.close[j] / self.close[ref] - 1
        bench = self.bench[j] / self.bench[ref] - 1 if self.bench[ref] > 0 else 0.0
        atr_pct = self.atr_pct[ref]
        if not np.isfinite(atr_pct) or atr_pct <= 0:
            return None, 1.0, None, None
        z = (stock - bench) * 100 / atr_pct
        move = POS if z > cfg.threshold_atr else NEG if z < -cfg.threshold_atr else "NO"
        known_at = session_close(self.dates[j], self.close_time)
        if sentiment > cfg.neutral_band:
            news = POS
        elif sentiment < -cfg.neutral_band:
            news = NEG
        else:
            implied = float(np.clip(z / 3, -cfg.implied_max, cfg.implied_max))
            if cfg.infer_neutral == "off" or (cfg.infer_neutral == "negative" and implied >= 0):
                implied = None
            return f"NEUTRAL_NEWS_{move}_REACTION", 1.0, known_at, implied
        label = f"{news}_NEWS_{move}_REACTION"
        return label, cfg.multipliers.get(label, 1.0), known_at, None
