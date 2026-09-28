import math
import numpy as np
import pandas as pd
from .config import Settings
from .data import MarketDataProvider
from .indicators import calculate

WEIGHTS = {"trend": 20, "momentum": 15, "volume": 10, "relative_strength": 10,
           "market_regime": 10, "volatility": 5}


def market_regime(index: pd.DataFrame) -> str:
    row = index.iloc[-1]
    if row.atr / row.close > 0.04:
        return "HIGH_VOLATILITY"
    if row.close > row.ema50 > row.ema200 and row.macd > 0:
        return "BULL"
    if row.close < row.ema50 < row.ema200:
        return "BEAR"
    return "NEUTRAL"


def risk_plan(entry: float, atr: float, config: Settings) -> dict:
    distance = atr * config.atr_stop_multiplier
    if not all(math.isfinite(v) and v > 0 for v in (entry, atr, distance)) or entry <= distance:
        raise ValueError("Invalid ATR stop distance")
    quantity = math.floor(min(config.equity*config.risk_fraction/distance,
                              config.equity*config.max_position_fraction/entry))
    if quantity < 1:
        raise ValueError("Risk budget cannot fund one share")
    return {"reference_entry": entry, "stop": entry-distance,
            "tp1": entry+config.tp1_r*distance, "tp2": entry+config.tp2_r*distance,
            "risk_reward": config.tp2_r, "quantity": quantity,
            "planned_risk_try": quantity*distance,
            "timing": "T close reference only; recompute using T+1 executable price"}


def score(features: pd.DataFrame, index: pd.DataFrame, config: Settings) -> dict:
    row, prior = features.iloc[-1], features.iloc[-2]
    regime = market_regime(index)
    relative = {str(n): float(row.close/features.close.iloc[-n-1] - index.close.iloc[-1]/index.close.iloc[-n-1]) for n in (5, 20, 60)}
    components = {}
    reasons = {"positive": [], "negative": []}

    def points(name, rules):
        total = 0.0
        for passed, weight, description in rules:
            total += weight if passed else 0
            reasons["positive" if passed else "negative"].append(description + (" ✓" if passed else " ✗"))
        components[name] = round(total, 4)

    points("trend", [(row.close > row.ema20, 2, "Close > EMA20"),
                     (row.close > row.ema50, 3, "Close > EMA50"),
                     (row.close > row.ema200, 3, "Close > EMA200"),
                     (row.ema20 > row.ema50, 2, "EMA20 > EMA50"),
                     (row.ema50 > row.ema200, 3, "EMA50 > EMA200"),
                     (row.adx > 25 and row.close > row.ema50, 2, "Directional trend with ADX > 25")])
    components["trend"] += float(5*row.trend_persistence)
    points("momentum", [(45 <= row.rsi <= 70, 5, "RSI in 45–70 range"),
                        (row.macd > row.macd_signal, 5, "MACD > signal"),
                        (row.macd_hist > prior.macd_hist, 5, "MACD histogram increasing")])
    points("volume", [(row.volume_ratio > 1.2 and row.close > prior.close, 5, "Rising close with volume > 1.2x"),
                      (row.volume_ratio > 1.5 and row.close > row.bb_upper, 5, "Bollinger breakout with volume > 1.5x")])
    points("relative_strength", [(relative[str(n)] > 0, weight, f"Outperformance vs benchmark over {n} sessions") for n, weight in ((5, 2), (20, 4), (60, 4))])
    components["market_regime"] = {"BULL": 10, "NEUTRAL": 5, "BEAR": 0, "HIGH_VOLATILITY": 0}[regime]
    points("volatility", [(0 < row.atr/row.close <= 0.04, 5, "ATR <= 4% of price")])
    raw = sum(components.values())
    normalized = raw / sum(WEIGHTS.values()) * 100
    blockers = []
    if regime in ("BEAR", "HIGH_VOLATILITY"):
        blockers.append("Market regime blocks long candidates")
    if row.turnover20 < config.minimum_turnover:
        blockers.append("Average turnover below minimum")
    try:
        plan = risk_plan(float(row.close), float(row.atr), config)
    except ValueError as error:
        plan = None
        blockers.append(str(error))
    threshold = 75 if regime == "BULL" else 80
    signal = "NO_TRADE" if normalized < 50 else "WATCH" if normalized < 65 else "WEAK_SETUP"
    if normalized >= threshold and not blockers:
        signal = "STRONG_BUY_CANDIDATE" if normalized >= 85 else "BUY_CANDIDATE"
    if blockers:
        signal = "NO_TRADE"
    return {"score": round(normalized, 2), "score_basis": "TECHNICAL_ONLY_NORMALIZED",
            "raw_points": round(raw, 2), "available_points": 70, "coverage_pct": 70,
            "unavailable_factors": ["news_kap", "institutional_flow"],
            "components": components, "component_maxima": WEIGHTS,
            "signal": signal, "market_regime": regime, "relative_strength": relative,
            "risk_plan": plan, "blockers": blockers, "reasons": reasons,
            "indicators": {key: float(row[key]) for key in ("close", "ema20", "ema50", "ema100", "ema200", "rsi", "macd", "macd_signal", "macd_hist", "bb_upper", "bb_lower", "atr", "adx", "volume_ratio")}}


def scan(provider: MarketDataProvider, config: Settings, as_of: pd.Timestamp, demo: bool = False) -> dict:
    benchmark = provider.daily_bars(config.benchmark, as_of)
    def check(bars):
        if len(bars) < config.minimum_history:
            raise ValueError(f"Need >= {config.minimum_history} daily bars")
        if (as_of - bars.index[-1]).days > config.max_age_days:
            raise ValueError("Stale daily bars")
    check(benchmark)
    index = calculate(benchmark)
    signals, errors = [], []
    for symbol in config.symbols:
        try:
            bars = provider.daily_bars(symbol, as_of)
            check(bars)
            # Do not bridge missing sessions or compare different closes.
            if not bars.index[-config.minimum_history:].equals(benchmark.index[-config.minimum_history:]):
                raise ValueError("Stock and benchmark session calendars differ")
            features = calculate(bars)
            if not np.isfinite(features.iloc[-2:].to_numpy(dtype=float)).all():
                raise ValueError("Undefined indicators (insufficient history or zero volume)")
            result = score(features, index, config)
            result.update(symbol=symbol, date=str(bars.index[-1].date()))
            signals.append(result)
        except (ValueError, OSError, KeyError) as error:
            errors.append({"symbol": symbol, "error": str(error)})
    return {"mode": "DEMO_SYNTHETIC" if demo else "CSV_RESEARCH",
            "as_of": str(as_of.date()), "benchmark": config.benchmark,
            "signals": sorted(signals, key=lambda x: (-x["score"], x["symbol"])), "errors": errors}
