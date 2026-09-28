from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bist_quant.indicators._smoothing import ema
from bist_quant.indicators.features import compute_features, warmup_bars
from bist_quant.indicators.momentum import macd, roc, rsi
from bist_quant.indicators.relative_strength import relative_strength
from bist_quant.indicators.trend import adx
from bist_quant.indicators.volatility import atr, bollinger_bands, rolling_percentile_rank
from bist_quant.indicators.volume import accumulation_distribution, obv, volume_ratio

from .reference import ref_adx, ref_atr, ref_ema, ref_rsi


def _assert_series_close(actual: pd.Series, expected: list[float], tol: float = 1e-9) -> None:
    exp = np.array(expected, dtype=float)
    act = actual.to_numpy(dtype=float)
    assert np.array_equal(np.isnan(act), np.isnan(exp)), "NaN positions differ"
    mask = ~np.isnan(exp)
    np.testing.assert_allclose(act[mask], exp[mask], rtol=tol, atol=tol)


@pytest.mark.parametrize("period", [5, 20, 50])
def test_ema_matches_reference(bars, period):
    _assert_series_close(ema(bars["close"], period), ref_ema(bars["close"].tolist(), period))


def test_ema_of_constant_is_constant():
    s = pd.Series([7.0] * 60)
    assert np.allclose(ema(s, 20).dropna(), 7.0)


def test_rsi_matches_reference(bars):
    _assert_series_close(rsi(bars["close"], 14), ref_rsi(bars["close"].tolist(), 14))


def test_rsi_bounds_and_extremes(bars):
    values = rsi(bars["close"]).dropna()
    assert values.between(0, 100).all()
    rising = pd.Series(np.arange(1.0, 60.0))
    assert rsi(rising).dropna().eq(100).all()
    falling = pd.Series(np.arange(60.0, 1.0, -1))
    assert rsi(falling).dropna().eq(0).all()


def test_macd_components(bars):
    m = macd(bars["close"], 12, 26, 9)
    expected_line = ema(bars["close"], 12) - ema(bars["close"], 26)
    pd.testing.assert_series_equal(m["macd"], expected_line, check_names=False)
    pd.testing.assert_series_equal(m["macd_signal"], ema(expected_line, 9), check_names=False)
    np.testing.assert_allclose(m["macd_hist"].dropna(), (m["macd"] - m["macd_signal"]).dropna())
    # Signal line is defined only after slow + signal - 1 bars.
    assert m["macd_signal"].first_valid_index() == bars.index[26 + 9 - 2]


def test_bollinger_bands(bars):
    bb = bollinger_bands(bars["close"], 20, 2.0)
    window = bars["close"].iloc[-20:].to_numpy()
    mid, std = window.mean(), window.std(ddof=0)
    assert bb["bb_mid"].iloc[-1] == pytest.approx(mid)
    assert bb["bb_upper"].iloc[-1] == pytest.approx(mid + 2 * std)
    assert bb["bb_lower"].iloc[-1] == pytest.approx(mid - 2 * std)
    valid = bb.dropna()
    assert (valid["bb_upper"] >= valid["bb_mid"]).all()
    assert (valid["bb_lower"] <= valid["bb_mid"]).all()


def test_atr_matches_reference(bars):
    h, lo, c = (bars[k].tolist() for k in ("high", "low", "close"))
    _assert_series_close(atr(bars["high"], bars["low"], bars["close"], 14), ref_atr(h, lo, c, 14))
    assert (atr(bars["high"], bars["low"], bars["close"]).dropna() > 0).all()


def test_adx_matches_reference(bars):
    h, lo, c = (bars[k].tolist() for k in ("high", "low", "close"))
    exp_adx, exp_pdi, exp_mdi = ref_adx(h, lo, c, 14)
    out = adx(bars["high"], bars["low"], bars["close"], 14)
    _assert_series_close(out["plus_di"], exp_pdi)
    _assert_series_close(out["minus_di"], exp_mdi)
    _assert_series_close(out["adx"], exp_adx)
    assert out["adx"].dropna().between(0, 100).all()


def test_adx_strong_in_steady_trend():
    idx = pd.bdate_range("2024-01-01", periods=120)
    close = pd.Series(np.linspace(100, 200, 120), index=idx)
    out = adx(close * 1.01, close * 0.99, close, 14)
    assert out["adx"].iloc[-1] > 50
    assert out["plus_di"].iloc[-1] > out["minus_di"].iloc[-1]


def test_volume_indicators():
    idx = pd.bdate_range("2024-01-01", periods=25)
    vol = pd.Series([100.0] * 24 + [300.0], index=idx)
    vr = volume_ratio(vol, 20)
    assert vr["volume_ma"].iloc[-1] == pytest.approx((19 * 100 + 300) / 20)
    assert vr["volume_ratio"].iloc[-1] == pytest.approx(300 / 110)
    assert vr["volume_ratio"].iloc[:19].isna().all()

    close = pd.Series([10, 11, 10.5, 10.5, 12], dtype=float)
    v = pd.Series([100, 200, 300, 400, 500], dtype=float)
    assert obv(close, v).tolist() == [0, 200, -100, -100, 400]

    high, low = close + 1, close - 1
    ad = accumulation_distribution(high, low, close, v)
    assert ad.iloc[-1] == pytest.approx(0.0)  # close exactly mid-range each day


def test_roc():
    s = pd.Series([100.0, 110.0, 121.0])
    assert roc(s, 1).iloc[-1] == pytest.approx(10.0)


def test_relative_strength_alignment():
    idx = pd.bdate_range("2024-01-01", periods=30)
    stock = pd.Series(np.linspace(100, 130, 30), index=idx)
    bench = pd.Series(np.linspace(1000, 1100, 30), index=idx).drop(idx[25])  # missing index day
    rs = relative_strength(stock, bench, [5, 20])
    expected_stock = (stock.iloc[-1] / stock.iloc[-6] - 1) * 100
    expected_bench = (1100 / bench.loc[idx[-6]] - 1) * 100
    assert rs["rs_5d"].iloc[-1] == pytest.approx(expected_stock - expected_bench)
    assert not rs["rs_5d"].iloc[5:].isna().any()  # forward-filled gap, no NaN hole


def test_percentile_rank():
    s = pd.Series(np.arange(1.0, 101.0))
    r = rolling_percentile_rank(s, 50)
    assert r.iloc[-1] == pytest.approx(1.0)
    assert rolling_percentile_rank(-s, 50).iloc[-1] == pytest.approx(0.0)


def test_features_no_nan_after_warmup(bars, settings):
    bench = bars["close"] * 10
    f = compute_features(
        bars, settings.indicators, settings.scoring.rules, settings.risk, benchmark_close=bench
    )
    after = f.iloc[warmup_bars(settings.indicators) + settings.scoring.rules.squeeze_lookback :]
    assert len(after) > 0
    assert not after.isna().any().any(), after.columns[after.isna().any()].tolist()
