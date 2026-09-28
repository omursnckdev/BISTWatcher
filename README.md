# BIST Quant Scanner

An explainable swing-trading **signal scanner for Borsa İstanbul** (BIST 30 / 50 / 100).
Every stock gets a transparent 0–100 score built from several independent factors,
a regime-aware signal classification, and a mandatory risk plan (entry zone, ATR stop,
R-multiple targets, reward/risk, position size). No factor ever says "BUY" on its own.

This repository implements **Phase 1 (technical core)** of
[`BIST_Quant_Trading_Bot_Project_Spec.md`](BIST_Quant_Trading_Bot_Project_Spec.md).
It generates signals only and never places orders.

> Signals are strategy classifications computed at the session close. They are not
> investment advice or a promise of future returns. The thresholds are unvalidated
> starting hypotheses until Phase 2 (backtesting) has tested them.

```text
BIST QUANT SCANNER
Date: 2026-09-25    Provider: yahoo
Market regime (XU100): HIGH_VOLATILITY  [regime score 2.0/10, breadth 23% above EMA50]  -> BUY threshold 85
  index ATR% at 90% percentile of 252D range (1.28x median)

  #  SYMBOL  SCORE  SIGNAL                     CLOSE   R:R   TRND   MOM   VOL    RS   REG  VLTY
-----------------------------------------------------------------------------------------------
  1  KRDMD    74.2  WEAK_SETUP                 46.96   0.4   17.8  14.2   4.0  10.0   2.0   4.0
  2  BIMAS    65.4  WEAK_SETUP                424.75   0.4   19.0   6.8   4.0  10.0   2.0   4.0
  3  TOASO    64.9  WATCH                     290.00   2.5   14.0  12.4   3.0  10.0   2.0   4.0
  ...
```

## Quick start

Requires Python 3.11+ (3.12 recommended).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python -m bist_quant scan                        # configured universe (BIST30), live Yahoo data
python -m bist_quant scan --universe BIST100 --top 20
python -m bist_quant scan --symbols THYAO ASELS --detail
python -m bist_quant scan --min-signal WEAK_SETUP # hide WATCH / NO_TRADE rows
python -m bist_quant scan --as-of 2025-06-30     # replay a past session (only data <= that date)
python -m bist_quant scan --json out.json --save # JSON + CSVs in data/processed/
python -m bist_quant analyze THYAO               # full explained breakdown
python -m bist_quant regime                      # market regime only
python -m bist_quant universe --universe BIST50  # list symbols
python -m bist_quant scan --provider synthetic   # offline demo (fake data, clearly labelled)

pytest                                           # unit + integration tests (offline)
BIST_QUANT_NETWORK_TESTS=1 pytest -k live        # optional live Yahoo check
```

`bist-quant` is also installed as a console script (`bist-quant scan ...`). Add `-v` for
structured JSON logs on stderr, or set `LOG_LEVEL=INFO` in the environment or `.env`.

## Pipeline

```text
DATA ─► FEATURES ─► FACTORS ─► SCORE ─► RISK FILTER ─► SIGNAL
```

| Stage | Module | Notes |
|---|---|---|
| Market data | `data/market_data.py` | `MarketDataProvider` protocol; Yahoo (async httpx, retry/backoff), CSV, synthetic; on-disk raw cache |
| Data quality | `data/quality.py` | de-duplicate, drop invalid candles, split/dividend adjustment, partial-session removal, stale/suspended detection against the XU100 calendar |
| Universe | `data/universe.py`, `config/universe.yaml` | BIST30 / BIST50 / BIST100 / CUSTOM |
| Indicators | `indicators/` | EMA 20/50/100/200, RSI(14), MACD(12,26,9), Bollinger(20,2), ATR(14), ADX(14) ±DI, volume MA/ratio, OBV, A/D line, ROC, RS vs XU100 (5/20/60D) |
| Regime | `regime/market_regime.py` | BULL / NEUTRAL / BEAR / HIGH_VOLATILITY from XU100 plus universe breadth |
| Scoring | `scoring/` | eight weighted factors, each with its reasons |
| Risk | `risk/` | ATR stop, R targets, resistance-aware reward/risk, fixed-fractional sizing, trailing stop |
| Signal | `strategy/` | regime-dependent thresholds, R:R / liquidity / stale-data gates |
| Output | `reporting.py`, `main.py` | ranked table, detail view, JSON, CSV (`signals`, `daily_features`) |

## Scoring model

| Factor | Weight | Phase 1 rubric (raw points, then scaled to the weight) |
|---|---:|---|
| Technical trend | 20 | Close>EMA20 2, Close>EMA50 3, Close>EMA200 3, EMA20>EMA50 2, EMA50>EMA200 3, ADX≥25 with +DI>−DI 2 (20–25: 1), % of last 20 closes above EMA50 × 5 |
| Momentum | 15 | RSI zone (45–65: 5, 65–70: 4, >70: 2, 30–45: 1, <30: 0), MACD>signal 3, histogram rising 2, histogram>0 1, ROC(10)>0 2, share of last 10 days with RSI>50 × 2 |
| Volume | 10 | up-day volume ratio (≥2.0: 4, ≥1.5: 3, ≥1.2: 2, ≥0.8: 1); high-volume down day = 0 and flagged as distribution; light-volume pullback 2; OBV>EMA 2; A/D rising 2; Bollinger breakout on volume 2 |
| Relative strength | 10 | 5D / 20D / 60D excess return vs XU100 worth 2 / 4 / 4, each on a linear ramp (0 at −k, full at +k, k = 2·√(n/20) pp) so there is no knife-edge threshold |
| News / KAP | 15 | *disabled until Phase 3* |
| Institutional flow | 15 | *disabled until Phase 4* |
| Market regime | 10 | XU100 above EMA20 / EMA50 / EMA200 (2 each), EMA50>EMA200 2, MACD>0 1, breadth ≥50% of universe above EMA50 1 |
| Volatility / setup | 5 | ATR 1.5–5% of price 2, extension from EMA20 ≤2 ATR 2 (shallow pullback 1), Bollinger squeeze 1 |

With `renormalize_missing: true`, disabled factors are left out and the total is rescaled to
0–100 over the enabled weights (Phase 1: 70 points, so 56/70 becomes 80). Weights, rubric
thresholds and bands live in `config/scoring.yaml`.

### Signals

| Score | Signal |
|---|---|
| < 50 | `NO_TRADE` |
| 50 – 64 | `WATCH` |
| 65 – buy threshold | `WEAK_SETUP` |
| ≥ buy threshold | `BUY_CANDIDATE` |
| ≥ buy threshold + 10 | `STRONG_BUY_CANDIDATE` |

The buy threshold depends on the regime: BULL 75, NEUTRAL 80, HIGH_VOLATILITY 85, BEAR 88
(`strategy.buy_threshold`). A buy-level score is still downgraded to `WEAK_SETUP` unless it
has a valid risk plan with **reward/risk ≥ 2.0**. Illiquid symbols (20D average turnover below
50M TRY) and stale or suspended symbols become `NO_TRADE`. Set `block_buys_in_bear: true` to
disable longs in a BEAR regime entirely.

### Market regime

Rules are checked in order and the first match wins:
1. **BEAR:** XU100 < EMA50 < EMA200.
2. **HIGH_VOLATILITY:** ATR% is in the top decile of its 1-year history *and* at least 1.25× its median, or XU100 is ≥15% below its 60-day high.
3. **BULL:** close > EMA50 > EMA200 and MACD > 0.
4. **NEUTRAL:** anything else.

Breadth is always measured on the configured universe, so a symbol's score does not depend
on which other symbols you scan.

### Risk plan

- **Entry:** the signal is computed on the T close; execution is assumed at T+1, inside the entry zone of close ± 0.25 ATR.
- **Stop:** entry − 2 × ATR(14).
- **Targets:** TP1 = entry + 1.5R, TP2 = entry + 2.5R, where R = entry − stop.
- **Reward/risk:** measured to TP2, or to overhead resistance when that sits between entry and TP2. Resistance is the highest high of the prior 120 sessions, excluding the last 5, so the current move doesn't count as resistance against itself.
- **Position size:** risk 1% of equity (500,000 TRY default), capped at 20% of equity per position, rounded to whole lots.

## Configuration

| File | Contents |
|---|---|
| `config/settings.yaml` | data provider and cache, indicator periods, regime rules, thresholds, risk, liquidity |
| `config/scoring.yaml` | factor weights (must sum to 100), rubric thresholds, bands |
| `config/universe.yaml` | mode (`BIST30`, `BIST50`, `BIST100`, `CUSTOM`), symbol lists, sector map |
| `.env` (copy `.env.example`) | credentials and `LOG_LEVEL`. Phase 1 needs no keys |

Everything is validated by Pydantic at start-up. Invalid values (for example, weights not
summing to 100, or `ema_fast ≥ ema_medium`) stop the program with a clear error. Use
`--config-dir` to run with an alternative set of files.

### Data providers

- **`yahoo`** (default): keyless Yahoo Finance chart API. `THYAO` maps to `THYAO.IS` and the index to `XU100.IS`. Raw responses are cached in `data/raw/yahoo/<SYMBOL>.csv` for `cache_ttl_hours`. The cache always holds data through the latest session, and a file written before today's 18:15 close is refreshed after it. Use `--refresh` to force a download.
- **`csv`**: your own vendor export in `data/raw/<SYMBOL>.csv` with columns `date,open,high,low,close,volume[,adjusted_close]`.
- **`synthetic`**: deterministic fake data for demos and tests. Output is clearly labelled.

To add a vendor, implement `async get_daily_bars(symbol, start, end) -> DataFrame` and
register it in `build_provider`. Strategy code never imports a vendor.

## Guarantees and assumptions

- **No look-ahead.** Every indicator is causal. Tests check that features, index features and a full `--as-of` scan are identical whether or not future bars exist. While the BIST session is open (before 18:15 Istanbul time), today's partial bar is dropped.
- **Adjusted prices.** Indicators use split- and dividend-adjusted OHLC (Yahoo `adjclose`). The latest bar is unadjusted, so entry, stop and targets are tradable prices. Turnover uses raw prices.
- **Missing data is not invented.** Gaps are reported, never filled. A symbol more than 2 sessions behind the XU100 calendar, or with a last bar older than 5 days, is treated as stale or suspended.
- **Indicator conventions** match TA-Lib/Wilder: SMA-seeded EMA, Wilder RMA for RSI/ATR/ADX, population std for Bollinger. They are verified against loop-based reference implementations in `tests/unit/reference.py`.

### Known limitations (Phase 1)

- **Index membership:** the universe lists are a configured snapshot of index membership (tickers verified on Yahoo in Sep 2026, including KOZAL→TRALT and KOZAA→TRMET). Keep them in sync with Borsa İstanbul's quarterly announcements.
- **Survivorship bias:** `--as-of` replays use *today's* universe. Historical index membership is needed before any serious backtest.
- **Sector relative strength** needs sector index data and is not scored yet. The sector map in `universe.yaml` is prepared for it.
- **Yahoo data** is free and unofficial: it can have gaps, delays or late corporate-action adjustments. Use a licensed vendor through the provider interface for production.
- **Unvalidated parameters:** all thresholds and weights are starting hypotheses. Phase 2 must test them statistically, including robustness across parameter ranges.

## Project layout

```text
config/                 settings.yaml, scoring.yaml, universe.yaml
src/bist_quant/
  main.py               CLI (python -m bist_quant ...)
  config.py             Pydantic settings + YAML/.env loading
  logging.py            structured JSON logging
  scanner.py            orchestration: data -> features -> score -> signal
  reporting.py          table / detail / JSON / CSV output
  models/               market.py, signals.py (domain models)
  data/                 market_data.py (providers), quality.py, universe.py
  indicators/           trend, momentum, volatility, volume, relative_strength, features
  regime/               market_regime.py
  scoring/              technical_score.py, composite_score.py
  strategy/             entry.py (classification), filters.py (liquidity, stale data)
  risk/                 stop_loss, take_profit, position_size, plan
tests/unit, tests/integration
data/raw, data/processed, data/cache   (git-ignored)
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Technical scanner, regime, scoring, risk plan, CLI, tests | ✅ this repo |
| 2 | Backtesting: costs, stops/targets, metrics, benchmarks, walk-forward | next |
| 3 | News & KAP ingestion, LLM classification (metadata only), decay, news score | planned |
| 4 | Broker/institutional flow (incl. BofA), custody data, flow score | planned |
| 5 | Composite model, ablation, out-of-sample validation | planned |
| 6–8 | Dashboard, paper trading, optional broker execution | planned |

`score_components(..., extra={"news": ..., "institutional_flow": ...})` is the hook through
which Phase 3 and 4 scores plug in without touching the technical code.
