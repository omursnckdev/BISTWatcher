import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from bist_quant.cli import main
from bist_quant.config import Settings
from bist_quant.data import DemoProvider, CsvProvider, validate_bars
from bist_quant.engine import scan, risk_plan, score
from bist_quant.indicators import calculate, wilder


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = pd.Timestamp("2026-09-25")
        self.provider = DemoProvider()
        self.bars = self.provider.daily_bars("THYAO", self.cutoff)

    def test_wilder_hand_calculated(self):
        result = wilder(pd.Series([1., 2., 3., 6., 9.]), 3)
        self.assertTrue(result.iloc[:2].isna().all())
        np.testing.assert_allclose(result.iloc[2:], [2, 10/3, 47/9])

    def test_flat_market(self):
        bars = self.bars.copy()
        bars.loc[:, ["open", "high", "low", "close"]] = 100
        row = calculate(bars).iloc[-1]
        self.assertEqual(row.rsi, 50)
        self.assertEqual(row.atr, 0)
        self.assertEqual(row.macd, 0)
        self.assertEqual(row.bb_upper, 100)
        with self.assertRaises(ValueError):
            risk_plan(100, 0, Settings())

    def test_rising_rsi(self):
        bars = self.bars.copy()
        bars["close"] = np.arange(len(bars)) + 100.
        self.assertEqual(calculate(bars).rsi.iloc[-1], 100)

    def test_no_future_indicator_leakage(self):
        prefix = calculate(self.bars.iloc[:-20])
        complete = calculate(self.bars).iloc[:-20]
        pd.testing.assert_frame_equal(prefix, complete)

    def test_invalid_and_duplicate_bars(self):
        frame = self.bars.reset_index()
        with self.assertRaises(ValueError):
            validate_bars(pd.concat([frame, frame.iloc[-1:]]), self.cutoff)
        frame.loc[0, "high"] = 0
        with self.assertRaises(ValueError):
            validate_bars(frame, self.cutoff)

    def test_risk_limits(self):
        config = Settings()
        result = risk_plan(100, 3, config)
        self.assertEqual(result["stop"], 94)
        self.assertEqual(result["tp2"], 115)
        self.assertEqual(result["quantity"], 833)
        self.assertLessEqual(result["planned_risk_try"], config.equity*config.risk_fraction)
        self.assertLessEqual(result["quantity"]*100, config.equity*config.max_position_fraction)

    def test_scan_bounds_and_missing_factors(self):
        report = scan(self.provider, Settings(), self.cutoff, True)
        self.assertFalse(report["errors"])
        self.assertEqual(len(report["signals"]), 5)
        for row in report["signals"]:
            self.assertTrue(0 <= row["score"] <= 100)
            self.assertEqual(row["coverage_pct"], 70)
            for key, value in row["components"].items():
                self.assertTrue(0 <= value <= row["component_maxima"][key])
        json.dumps(report, allow_nan=False)

    def test_bear_blocks_even_high_score(self):
        features = calculate(self.bars)
        index = calculate(self.provider.daily_bars("XU100", self.cutoff))
        index.loc[index.index[-1], ["close", "ema50", "ema200", "atr"]] = [100, 110, 120, 1]
        result = score(features, index, Settings())
        self.assertEqual(result["signal"], "NO_TRADE")
        self.assertTrue(result["blockers"])

    def test_csv_point_in_time_and_calendar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for symbol in ("THYAO", "XU100"):
                bars = self.provider.daily_bars(symbol, self.cutoff + pd.Timedelta(days=10))
                bars.to_csv(root / f"{symbol}.csv")
            config = Settings(symbols=("THYAO",))
            report = scan(CsvProvider(root), config, self.cutoff)
            self.assertFalse(report["errors"])
            self.assertEqual(report["signals"][0]["date"], "2026-09-25")
            bars = pd.read_csv(root / "THYAO.csv")
            bars = bars[bars.date != "2026-09-24"]
            bars.to_csv(root / "THYAO.csv", index=False)
            self.assertTrue(scan(CsvProvider(root), config, self.cutoff)["errors"])

    def test_stale_and_insufficient_history(self):
        for frame in (self.bars.iloc[:-10], self.bars.iloc[-100:]):
            class Provider:
                def daily_bars(self, symbol, as_of):
                    return frame
            with self.assertRaises(ValueError):
                scan(Provider(), Settings(), self.cutoff)

    def test_invalid_config(self):
        for kwargs in ({"risk_fraction": 2}, {"symbols": ("../secret",)}, {"equity": float("nan")}, {"minimum_history": 10}):
            with self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_cli_json(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status = main(["--demo", "--as-of", "2026-09-25", "--json"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(buffer.getvalue())["mode"], "DEMO_SYNTHETIC")


if __name__ == "__main__":
    unittest.main()
