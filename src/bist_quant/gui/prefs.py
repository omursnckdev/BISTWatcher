"""User preferences and holdings, stored as JSON in the user data directory."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any

from bist_quant.strategy.exit_check import Holding

PREFS_FILE = "tercihler.json"
HOLDINGS_FILE = "portfoy.json"


@dataclass
class Prefs:
    provider: str = "yahoo"  # yahoo | csv | synthetic
    universe: str = "BIST30"  # BIST30 | BIST50 | BIST100 | CUSTOM
    custom_symbols: list[str] = field(default_factory=list)
    news: bool = True
    buy_threshold: float | None = None  # BULL threshold; None = config value
    use_resistance_cap: bool | None = None
    minimum_rr: float | None = None
    portfolio_equity: float | None = None
    risk_per_trade_pct: float | None = None
    block_buys_in_bear: bool | None = None
    backtest_start: str = "2020-01-02"

    def overrides(self, bull_threshold: float) -> dict[str, Any]:
        """Dotted-path setting overrides; ``None`` fields keep the YAML value."""
        out: dict[str, Any] = {"data.provider": self.provider}
        if self.buy_threshold is not None:
            out["strategy.buy_threshold_offset"] = self.buy_threshold - bull_threshold
        pairs = {
            "risk.use_resistance_cap": self.use_resistance_cap,
            "risk.minimum_rr": self.minimum_rr,
            "risk.portfolio_equity": self.portfolio_equity,
            "risk.risk_per_trade_pct": self.risk_per_trade_pct,
            "strategy.block_buys_in_bear": self.block_buys_in_bear,
        }
        out.update({k: v for k, v in pairs.items() if v is not None})
        return out


def load_prefs(home: Path) -> Prefs:
    path = home / PREFS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Prefs()
    known = {f.name for f in fields(Prefs)}
    try:
        return Prefs(**{k: v for k, v in raw.items() if k in known})
    except TypeError:
        return Prefs()


def save_prefs(home: Path, prefs: Prefs) -> None:
    (home / PREFS_FILE).write_text(
        json.dumps(asdict(prefs), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_holdings(home: Path) -> list[Holding]:
    path = home / HOLDINGS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append(
                Holding(
                    symbol=str(item["symbol"]).upper(),
                    entry_date=date.fromisoformat(item["entry_date"]),
                    entry_price=float(item["entry_price"]),
                    shares=int(item["shares"]),
                    stop=None if item.get("stop") in (None, "") else float(item["stop"]),
                    tp1_taken=bool(item.get("tp1_taken", False)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_holdings(home: Path, holdings: list[Holding]) -> None:
    data = [{**asdict(h), "entry_date": h.entry_date.isoformat()} for h in holdings]
    (home / HOLDINGS_FILE).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
