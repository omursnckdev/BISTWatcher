import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import sys
import pandas as pd
from .config import load_settings
from .data import CsvProvider, DemoProvider
from .engine import scan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BISTWatcher daily technical research scanner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="Use synthetic data; no market quotes")
    source.add_argument("--data-dir", type=Path, help="Directory containing SYMBOL.csv files")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--as-of", help="Last COMPLETED session cutoff YYYY-MM-DD (required for CSV)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable report")
    parser.add_argument("--output", type=Path, help="Also write full JSON report")
    args = parser.parse_args(argv)
    if not args.demo and not args.as_of:
        parser.error("--as-of is required for CSV; use only completed daily candles")
    try:
        date_text = args.as_of or datetime.now(ZoneInfo("Europe/Istanbul")).date().isoformat()
        cutoff = pd.Timestamp(datetime.strptime(date_text, "%Y-%m-%d").date())
        config = load_settings(args.config)
        report = scan(DemoProvider() if args.demo else CsvProvider(args.data_dir), config, cutoff, args.demo)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded+"\n", encoding="utf-8")
        if args.json:
            print(encoded)
        else:
            print(f"BISTWatcher | {report['mode']} | {report['as_of']}")
            print("Technical score /100; factor coverage 70/100. News and flow unavailable.")
            for row in report["signals"]:
                print(f"{row['symbol']:8} {row['score']:6.2f} {row['signal']:22} {row['market_regime']}")
            for error in report["errors"]:
                print(f"ERROR {error['symbol']}: {error['error']}", file=sys.stderr)
        return 1 if report["errors"] else 0
    except (ValueError, TypeError, OSError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
