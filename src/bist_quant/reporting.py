"""Plain-text and JSON renderers for scan results."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from bist_quant.models.signals import SignalResult, SignalType
from bist_quant.scanner import ScanResult

COMPONENT_LABELS = {
    "trend": "Technical Trend",
    "momentum": "Momentum",
    "volume": "Volume",
    "relative_strength": "Relative Strength",
    "news": "News / KAP",
    "institutional_flow": "Institutional Flow",
    "market_regime": "Market Regime",
    "volatility": "Volatility / Setup",
}
_SHORT = {
    "trend": "TRND",
    "momentum": "MOM",
    "volume": "VOL",
    "relative_strength": "RS",
    "market_regime": "REG",
    "volatility": "VLTY",
}

SIGNAL_RANK = {s: i for i, s in enumerate(SignalType)}


def _fmt_price(x: float | None) -> str:
    return "-" if x is None else f"{x:,.2f}"


def format_header(result: ScanResult) -> str:
    r = result.regime
    provider = f"Provider: {result.provider}"
    if result.provider == "synthetic":
        provider += "    ** SYNTHETIC DATA - NOT REAL PRICES **"
    regime = f"Market regime ({r.index}): {r.regime.value}  [regime score "
    regime += f"{r.score.points:.1f}/{r.score.max_points:.0f}"
    if r.breadth_pct is not None:
        regime += f", breadth {r.breadth_pct:.0f}% above EMA50"
    regime += "]"
    if result.signals:
        regime += f"  -> BUY threshold {result.signals[0].buy_threshold:.0f}"
    return "\n".join(
        [
            "BIST QUANT SCANNER",
            f"Date: {result.as_of.isoformat()}    {provider}",
            regime,
            "  " + "; ".join(r.reasons),
        ]
    )


def _signal_label(s: SignalResult) -> str:
    """Signal text for the table; a filtered NO_TRADE says why."""
    if not s.data_ok:
        return f"{s.signal.value} (stale)"
    if not s.liquidity_ok:
        return f"{s.signal.value} (illiquid)"
    return s.signal.value


def format_table(result: ScanResult, top: int | None = None) -> str:
    rows = result.signals[:top] if top else result.signals
    comp_keys = [k for k in _SHORT if rows and rows[0].components[k].enabled]
    head = f"{'#':>3}  {'SYMBOL':<7} {'SCORE':>5}  {'SIGNAL':<21} {'CLOSE':>10} {'R:R':>5}  "
    head += " ".join(f"{_SHORT[k]:>5}" for k in comp_keys)
    out = [format_header(result), "", head, "-" * len(head)]
    for i, s in enumerate(rows, 1):
        rr = f"{s.risk.risk_reward:.1f}" if s.risk else "-"
        comps = " ".join(f"{s.components[k].points:5.1f}" for k in comp_keys)
        out.append(
            f"{i:>3}  {s.symbol:<7} {s.score:5.1f}  {_signal_label(s):<21} "
            f"{_fmt_price(s.close):>10} {rr:>5}  {comps}"
        )
    counts = pd.Series([s.signal.value for s in result.signals]).value_counts()
    out.append("")
    out.append("Signals: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    components = rows[0].components.items() if rows else []
    disabled = [COMPONENT_LABELS[k] for k, c in components if not c.enabled]
    if disabled:
        out.append(
            f"Note: {', '.join(disabled)} not yet enabled; "
            "score is re-normalised over the enabled factors."
        )
    if result.skipped:
        out.append("Skipped: " + "; ".join(f"{k} ({v})" for k, v in sorted(result.skipped.items())))
    out.append(
        "Signals are strategy classifications computed at the session close, not "
        "investment advice or a promise of future returns."
    )
    return "\n".join(out)


def format_detail(s: SignalResult) -> str:
    out = [f"{s.symbol}    {s.date.isoformat()}", ""]
    for key, comp in s.components.items():
        label = COMPONENT_LABELS.get(key, key)
        if comp.enabled:
            out.append(f"{label:<22}{comp.points:5.1f}/{comp.max_points:.0f}")
        else:
            out.append(f"{label:<22}  n/a/{comp.max_points:.0f}  (disabled)")
    out += [
        "",
        f"TOTAL SCORE:          {s.score:5.1f}/100",
        f"Signal:               {s.signal.value}",
        f"Market regime:        {s.market_regime.value} (BUY threshold {s.buy_threshold:.0f})",
        "",
    ]
    if s.risk:
        p = s.risk
        out += [
            f"Close:        {_fmt_price(s.close)}",
            f"Entry Zone:   {_fmt_price(p.entry_zone_low)} - {_fmt_price(p.entry_zone_high)}"
            "   (signal at close T, execution T+1)",
            f"Stop Loss:    {_fmt_price(p.stop)}",
            f"TP1 / TP2:    {_fmt_price(p.tp1)} / {_fmt_price(p.tp2)}",
            f"Risk/Reward:  {p.risk_reward:.2f}"
            + (
                f"  (capped by resistance {_fmt_price(p.resistance)})"
                if p.resistance_capped
                else ""
            ),
            f"Position:     {p.shares:,} shares = {p.position_value:,.0f} TRY, "
            f"risking {p.capital_at_risk:,.0f} TRY",
            "",
        ]
    ind = s.indicators
    if ind:

        def g(k: str, fmt: str = "{:.2f}") -> str:
            return "-" if ind.get(k) is None else fmt.format(ind[k])

        out += [
            "Indicators:",
            f"  EMA20 {g('ema_fast')}  EMA50 {g('ema_medium')}  EMA200 {g('ema_slow')}",
            f"  RSI {g('rsi', '{:.1f}')}  MACD {g('macd', '{:.3f}')} / signal "
            f"{g('macd_signal', '{:.3f}')}  ADX {g('adx', '{:.1f}')}",
            f"  ATR {g('atr')} ({g('atr_pct', '{:.1f}')}%)  "
            f"Volume {g('volume_ratio', '{:.2f}')}x 20D avg"
            f"  BB {g('bb_lower')} / {g('bb_upper')}",
            "",
        ]
    out.append("Reasons:")
    out += [f"  + {r}" for r in s.explanation.positive_factors]
    out += [f"  - {r}" for r in s.explanation.negative_factors]
    out += [f"  · {r}" for r in s.explanation.notes]
    if s.explanation.filters:
        out.append("Filters:")
        out += [f"  ! {r}" for r in s.explanation.filters]
    return "\n".join(out)


def to_json(result: ScanResult) -> str:
    payload = {
        "as_of": result.as_of.isoformat(),
        "provider": result.provider,
        "regime": result.regime.model_dump(mode="json"),
        "signals": [
            {
                **s.model_dump(mode="json", exclude={"components"}),
                "components": s.component_points(),
                "component_details": {
                    k: c.model_dump(mode="json") for k, c in s.components.items()
                },
            }
            for s in result.signals
        ],
        "skipped": result.skipped,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def save_outputs(result: ScanResult, out_dir: Path) -> list[Path]:
    """Write the signal table and the latest feature row per symbol (daily_features)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.as_of.strftime("%Y%m%d")
    signals_path = out_dir / f"signals_{stamp}.csv"
    rows = []
    for s in result.signals:
        row = {
            "symbol": s.symbol,
            "date": s.date,
            "signal_type": s.signal.value,
            "score": s.score,
            "market_regime": s.market_regime.value,
            "entry_price": s.risk.entry if s.risk else None,
            "stop_price": s.risk.stop if s.risk else None,
            "tp1": s.risk.tp1 if s.risk else None,
            "tp2": s.risk.tp2 if s.risk else None,
            "risk_reward": s.risk.risk_reward if s.risk else None,
            "explanation_json": json.dumps(s.explanation.model_dump(), ensure_ascii=False),
        }
        row.update({f"{k}_score": v for k, v in s.component_points().items()})
        rows.append(row)
    pd.DataFrame(rows).to_csv(signals_path, index=False)

    features_path = out_dir / f"daily_features_{stamp}.csv"
    latest = []
    by_symbol = {s.symbol: s for s in result.signals}
    for sym, frame in result.features.items():
        rec = frame.iloc[-1].to_dict()
        rec.update(
            symbol=sym,
            date=frame.index[-1].date(),
            market_regime=result.regime.regime.value,
            total_score=by_symbol[sym].score if sym in by_symbol else None,
        )
        latest.append(rec)
    pd.DataFrame(latest).set_index(["symbol", "date"]).to_csv(features_path)
    return [signals_path, features_path]
