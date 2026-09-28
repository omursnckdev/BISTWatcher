"""Command-line interface.

Examples::

    python -m bist_quant scan                       # configured universe (BIST30)
    python -m bist_quant scan --universe BIST100 --top 20
    python -m bist_quant scan --symbols THYAO ASELS --detail
    python -m bist_quant scan --provider synthetic  # offline demo data
    python -m bist_quant scan --as-of 2025-06-30    # replay using data <= that date
    python -m bist_quant analyze THYAO
    python -m bist_quant regime
    python -m bist_quant universe --universe BIST50
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from bist_quant import __version__
from bist_quant.config import Settings, load_settings
from bist_quant.data.market_data import CachingProvider, MarketDataProvider, build_provider
from bist_quant.data.universe import load_universe, normalize_symbol
from bist_quant.logging import configure_logging
from bist_quant.models.signals import SignalType
from bist_quant.reporting import (
    SIGNAL_RANK,
    format_detail,
    format_header,
    format_table,
    save_outputs,
    to_json,
)
from bist_quant.scanner import ScanError, ScanResult, run_scan


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bist_quant", description="Explainable BIST swing-trading scanner (Phase 1)."
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config-dir", type=Path, help="directory containing the YAML config files")
    p.add_argument("-v", "--verbose", action="store_true", help="structured INFO logs on stderr")
    sub = p.add_subparsers(dest="command", required=True)

    def data_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--provider", choices=["yahoo", "csv", "synthetic"])
        sp.add_argument("--refresh", action="store_true", help="ignore the raw-data cache")
        sp.add_argument(
            "--as-of",
            type=date.fromisoformat,
            metavar="YYYY-MM-DD",
            help="evaluate as of a past session using only data up to that date",
        )

    scan = sub.add_parser("scan", help="scan the universe and print a ranked table")
    data_args(scan)
    scan.add_argument("--universe", help="BIST30 | BIST50 | BIST100 | CUSTOM | <list name>")
    scan.add_argument("--symbols", nargs="+", help="explicit symbols (overrides --universe)")
    scan.add_argument("--top", type=int, help="show only the top N rows")
    scan.add_argument(
        "--min-signal",
        choices=[s.value for s in SignalType],
        help="hide rows below this signal level",
    )
    scan.add_argument("--detail", action="store_true", help="print the full breakdown per symbol")
    scan.add_argument("--json", type=Path, metavar="PATH", help="write JSON ('-' = stdout)")
    scan.add_argument(
        "--save",
        action="store_true",
        help="write signals and daily features CSVs to data/processed/",
    )

    analyze = sub.add_parser("analyze", help="detailed, explained analysis for symbols")
    data_args(analyze)
    analyze.add_argument("symbols", nargs="+")

    regime = sub.add_parser("regime", help="show the current market regime")
    data_args(regime)
    regime.add_argument("--universe", help="universe used for the breadth measure")

    uni = sub.add_parser("universe", help="list the symbols of a universe")
    uni.add_argument("--universe")
    return p


def _provider(settings: Settings, args: argparse.Namespace) -> MarketDataProvider:
    if getattr(args, "provider", None):
        settings.data.provider = args.provider
    provider = build_provider(settings)
    if isinstance(provider, CachingProvider) and getattr(args, "refresh", False):
        provider.force_refresh = True
    return provider


async def _scan(
    settings: Settings, symbols: list[str], breadth: list[str], args: argparse.Namespace
) -> ScanResult:
    provider = _provider(settings, args)
    try:
        return await run_scan(
            settings, symbols, provider, as_of=args.as_of, breadth_symbols=breadth
        )
    finally:
        inner = getattr(provider, "inner", provider)
        if hasattr(inner, "aclose"):
            await inner.aclose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging("INFO" if args.verbose else None)
    try:
        settings = load_settings(args.config_dir)
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "universe":
            for sym in load_universe(settings.universe, args.universe):
                print(sym)
            return 0

        universe = load_universe(settings.universe, getattr(args, "universe", None))
        if getattr(args, "symbols", None):
            symbols = [normalize_symbol(s) for s in args.symbols]
        elif args.command == "regime":
            symbols = []
        else:
            symbols = universe
        # Market breadth is always measured on the configured universe.
        result = asyncio.run(_scan(settings, symbols, universe, args))
    except ScanError as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.command == "regime":
        print(format_header(result))
        return 0

    if args.command == "analyze":
        print(format_header(result))
        for s in result.signals:
            print("\n" + "=" * 60 + "\n" + format_detail(s))
        for sym, why in result.skipped.items():
            print(f"\n{sym}: skipped ({why})")
        return 0 if result.signals else 1

    if args.min_signal:
        floor = SIGNAL_RANK[SignalType(args.min_signal)]
        result.signals = [s for s in result.signals if SIGNAL_RANK[s.signal] >= floor]
    if args.json and str(args.json) == "-":
        print(to_json(result))
        return 0
    print(format_table(result, args.top))
    if args.detail:
        for s in result.signals[: args.top] if args.top else result.signals:
            print("\n" + "=" * 60 + "\n" + format_detail(s))
    if args.json:
        args.json.write_text(to_json(result), encoding="utf-8")
        print(f"\nJSON written to {args.json}")
    if args.save:
        for path in save_outputs(result, settings.resolve_path(Path("data/processed"))):
            print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
