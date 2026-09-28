"""Historical scoring and signal classification over the whole panel.

Uses exactly the scanner's rules (``strategy.evaluate``), one row per symbol-session.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bist_quant.backtest.data import FeatureCache, _key
from bist_quant.config import Settings
from bist_quant.models.signals import ComponentScore, MarketRegime, SignalType
from bist_quant.regime.market_regime import classify_row, regime_score
from bist_quant.strategy.evaluate import decide_row, score_row

BUY_SIGNALS = {SignalType.BUY_CANDIDATE, SignalType.STRONG_BUY_CANDIDATE}
COMPONENTS = ["trend", "momentum", "volume", "relative_strength", "market_regime", "volatility"]


@dataclass
class RegimeHistory:
    frame: pd.DataFrame  # index=date: regime, points, breadth
    components: dict[pd.Timestamp, ComponentScore]


@dataclass
class ScoredPanel:
    """Per-symbol score history (``score`` + component points) and regime history."""

    scores: dict[str, pd.DataFrame]
    regime: RegimeHistory


def breadth_history(features: dict[str, pd.DataFrame], min_symbols: int) -> pd.Series:
    above = pd.DataFrame(
        {
            s: (f["close"] > f["ema_medium"]).astype(float).where(f["ema_medium"].notna())
            for s, f in features.items()
        }
    )
    breadth = above.mean(axis=1, skipna=True) * 100
    return breadth.where(above.notna().sum(axis=1) >= min_symbols)


def regime_history(
    index_features: pd.DataFrame,
    breadth: pd.Series,
    settings: Settings,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> RegimeHistory:
    cfg = settings.market_regime
    weight = settings.scoring.weights.market_regime
    window = index_features.loc[start:end].dropna(subset=["ema_slow", "macd"])
    breadth = breadth.reindex(window.index)
    rows, comps = [], {}
    for d, row in window.iterrows():
        b = breadth.get(d)
        b = None if pd.isna(b) else float(b)
        regime, _ = classify_row(row, cfg)
        comp = regime_score(row, weight, b, cfg)
        comps[d] = comp
        rows.append({"date": d, "regime": regime.value, "points": comp.points, "breadth": b})
    frame = (
        pd.DataFrame(rows).set_index("date")
        if rows
        else pd.DataFrame(columns=["regime", "points", "breadth"])
    )
    return RegimeHistory(frame, comps)


class Scorer:
    """Scores the panel once per scoring configuration and caches the result."""

    def __init__(self, cache: FeatureCache) -> None:
        self.cache = cache
        self._scored: dict[str, ScoredPanel] = {}

    def scored(self, settings: Settings) -> ScoredPanel:
        key = _key(
            settings.indicators,
            settings.scoring,
            settings.market_regime,
            settings.risk.resistance_lookback,
        )
        if key not in self._scored:
            self._scored[key] = self._score(settings)
        return self._scored[key]

    def _score(self, settings: Settings) -> ScoredPanel:
        hist = self.cache.history
        features = self.cache.features(settings)
        breadth = breadth_history(features, settings.market_regime.min_breadth_symbols)
        regime = regime_history(
            self.cache.index_features(settings), breadth, settings, hist.start, hist.end
        )
        scores: dict[str, pd.DataFrame] = {}
        for sym, f in features.items():
            window = f.loc[hist.start : hist.end]
            window = window[window.index.isin(regime.frame.index)]
            out = []
            for d, row in zip(window.index, window.to_dict("records"), strict=True):
                score, comps = score_row(row, regime.components[d], settings)
                rec = {"date": d, "score": score}
                rec.update({c: comps[c].points for c in COMPONENTS})
                out.append(rec)
            if out:
                scores[sym] = pd.DataFrame(out).set_index("date")
        return ScoredPanel(scores, regime)


@dataclass
class Candidate:
    symbol: str
    score: float
    signal: SignalType
    close: float
    atr: float
    risk_reward: float


def classify_panel(
    scored: ScoredPanel, features: dict[str, pd.DataFrame], settings: Settings
) -> tuple[dict[str, pd.Series], dict[pd.Timestamp, list[Candidate]]]:
    """Signal per symbol-session and the BUY candidates per session (best score first)."""
    regimes = scored.regime.frame["regime"]
    signals: dict[str, pd.Series] = {}
    candidates: dict[pd.Timestamp, list[Candidate]] = {}
    for sym, sc in scored.scores.items():
        f = features[sym].loc[sc.index]
        labels = []
        for d, row, score in zip(sc.index, f.to_dict("records"), sc["score"], strict=True):
            regime = MarketRegime(regimes[d])
            dec = decide_row(row, score, {}, regime, settings, always_plan=False)
            labels.append(dec.signal.value)
            if dec.signal in BUY_SIGNALS and dec.plan is not None:
                candidates.setdefault(d, []).append(
                    Candidate(
                        sym, score, dec.signal, row["close"], row["atr"], dec.plan.risk_reward
                    )
                )
        signals[sym] = pd.Series(labels, index=sc.index, name="signal")
    for day in candidates.values():
        day.sort(key=lambda c: (-c.score, c.symbol))
    return signals, candidates
