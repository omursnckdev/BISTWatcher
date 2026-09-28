"""BIST symbol universe loader."""

from __future__ import annotations

import re

from bist_quant.config import UniverseSettings

_TICKER = re.compile(r"^[A-Z0-9]{3,6}$")
_COMPOSITE = {
    "BIST30": ["BIST30"],
    "BIST50": ["BIST30", "BIST50_EXTRA"],
    "BIST100": ["BIST30", "BIST50_EXTRA", "BIST100_EXTRA"],
}


def normalize_symbol(symbol: str) -> str:
    sym = symbol.strip().upper().removesuffix(".IS")
    if not _TICKER.match(sym):
        raise ValueError(f"invalid BIST ticker: {symbol!r}")
    return sym


def load_universe(universe: UniverseSettings, mode: str | None = None) -> list[str]:
    """Return the de-duplicated, ordered symbol list for ``mode`` (default: configured mode).

    ``mode`` may be ``BIST30``/``BIST50``/``BIST100``, ``CUSTOM`` (uses ``symbols``),
    or the name of any list defined under ``lists``.
    """
    mode = (mode or universe.mode).upper()
    if mode == "CUSTOM":
        raw = list(universe.symbols)
    elif mode in _COMPOSITE:
        raw = []
        for part in _COMPOSITE[mode]:
            if part not in universe.lists:
                raise ValueError(f"universe list {part!r} is not defined in universe.yaml")
            raw.extend(universe.lists[part])
    elif mode in universe.lists:
        raw = list(universe.lists[mode])
    else:
        known = sorted({"CUSTOM", *_COMPOSITE, *universe.lists})
        raise ValueError(f"unknown universe mode {mode!r}; expected one of {known}")
    seen: dict[str, None] = {}
    for sym in raw:
        seen.setdefault(normalize_symbol(sym), None)
    if not seen:
        raise ValueError(f"universe {mode!r} is empty")
    return list(seen)
