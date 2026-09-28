import numpy as np
import pandas as pd


def wilder(series: pd.Series, period: int = 14) -> pd.Series:
    """SMA-seeded Wilder smoothing; preserve the warm-up as NaN."""
    values = series.to_numpy(dtype=float)
    result = np.full(len(values), np.nan)
    previous = np.nan
    for i in range(period - 1, len(values)):
        if not np.isfinite(values[i]):
            previous = np.nan
        elif not np.isfinite(previous):
            window = values[i-period+1:i+1]
            if np.isfinite(window).all():
                previous = float(window.mean())
        else:
            previous = (previous * (period - 1) + values[i]) / period
        result[i] = previous
    return pd.Series(result, index=series.index)


def calculate(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    close = df.close
    for period in (20, 50, 100, 200):
        df[f"ema{period}"] = close.ewm(span=period, adjust=False, min_periods=period).mean()
    change = close.diff()
    gain, loss = wilder(change.clip(lower=0)), wilder(-change.clip(upper=0))
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    df.loc[(loss == 0) & (gain > 0), "rsi"] = 100
    df.loc[(loss == 0) & (gain == 0), "rsi"] = 50
    df["macd"] = close.ewm(span=12, adjust=False, min_periods=12).mean() - close.ewm(span=26, adjust=False, min_periods=26).mean()
    df["macd_signal"] = df.macd.ewm(span=9, adjust=False, min_periods=9).mean()
    df["macd_hist"] = df.macd - df.macd_signal
    df["bb_middle"] = close.rolling(20).mean()
    std = close.rolling(20).std(ddof=0)
    df["bb_upper"], df["bb_lower"] = df.bb_middle + 2*std, df.bb_middle - 2*std
    tr = pd.concat([df.high-df.low, (df.high-close.shift()).abs(), (df.low-close.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = wilder(tr)
    up, down = df.high.diff(), -df.low.diff()
    plus = up.where((up > down) & (up > 0), 0.0)
    minus = down.where((down > up) & (down > 0), 0.0)
    plus.iloc[0] = minus.iloc[0] = np.nan
    pdi, mdi = 100*wilder(plus)/df.atr, 100*wilder(minus)/df.atr
    denominator = pdi + mdi
    dx = (100*(pdi-mdi).abs()/denominator).mask(denominator == 0, 0)
    df["adx"] = wilder(dx)
    # Use the preceding 20 sessions as baseline; today's spike doesn't dilute itself.
    df["volume_ratio"] = df.volume / df.volume.shift().rolling(20).mean().replace(0, np.nan)
    df["turnover20"] = (df.close * df.volume).rolling(20).mean()
    df["trend_persistence"] = (close > df.ema50).astype(float).rolling(10).mean()
    return df
