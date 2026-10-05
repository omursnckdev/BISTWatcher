# BIST Quant Scanner: technical reference

> The Turkish user guide is the [README](../README.md). This page keeps the detailed
> English reference. Every command also has a Turkish name (`scan` = `tara`, `backtest` =
> `geritest`, ...); see the README for the full list.

An explainable swing-trading **signal scanner for Borsa İstanbul** (BIST 30 / 50 / 100).
Every stock gets a transparent 0–100 score built from several independent factors,
a regime-aware signal classification, and a mandatory risk plan (entry zone, ATR stop,
R-multiple targets, reward/risk, position size). No factor ever says "BUY" on its own.

This repository implements **Phase 1 (technical core)**, **Phase 2 (backtesting,
walk-forward validation, factor research)** and **Phase 3 (KAP disclosures / news)** of
[`BIST_Quant_Trading_Bot_Project_Spec.md`](../BIST_Quant_Trading_Bot_Project_Spec.md).
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
python -m venv .venv && source .venv/bin/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m bist_quant scan                        # configured universe (BIST100), live data + KAP news
python -m bist_quant scan --universe BIST100 --top 20
python -m bist_quant scan --symbols THYAO ASELS --detail
python -m bist_quant scan --min-signal WEAK_SETUP # hide WATCH / NO_TRADE rows
python -m bist_quant scan --as-of 2025-06-30     # replay a past session (only data <= that date)
python -m bist_quant scan --json out.json --save # JSON + CSVs in data/processed/
python -m bist_quant analyze THYAO               # full explained breakdown
python -m bist_quant regime                      # market regime only
python -m bist_quant universe --universe BIST50  # list symbols
python -m bist_quant scan --provider synthetic   # offline demo (fake data, clearly labelled)

python -m bist_quant backtest                    # 2016 -> today, costs, benchmarks (backtest.yaml)
python -m bist_quant backtest --universe BIST100 --set risk.use_resistance_cap=false
python -m bist_quant sweep --grid "strategy.buy_threshold_offset=[-10,-5,0,5,10]"
python -m bist_quant walkforward                 # choose params in-sample, test out-of-sample
python -m bist_quant research                    # does the score predict forward returns?
python -m bist_quant kap-sync                    # download KAP disclosure history (BIST100)
python -m bist_quant news ASELS THYAO            # classified KAP events + current news score
python -m bist_quant backtest --no-news          # ablation: technical factors only

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
| News / KAP | 15 | 7.5 neutral without news; point-in-time KAP impact via tanh (see News & KAP below) |
| Institutional flow | 15 | *disabled until Phase 4* |
| Market regime | 10 | XU100 above EMA20 / EMA50 / EMA200 (2 each), EMA50>EMA200 2, MACD>0 1, breadth ≥50% of universe above EMA50 1 |
| Volatility / setup | 5 | ATR 1.5–5% of price 2, extension from EMA20 ≤2 ATR 2 (shallow pullback 1), Bollinger squeeze 1 |

With `renormalize_missing: true`, disabled factors are left out and the total is rescaled to
0–100 over the enabled weights: 85 points with news, 70 without (for example, 56/70 becomes
80). A neutral news score of 7.5/15 pulls totals slightly toward 50, so compare runs with
and without news at similar trade counts (`sweep` over `strategy.buy_threshold_offset`). Weights, rubric
thresholds and bands live in `config/scoring.yaml`.

### Signals

| Score | Signal |
|---|---|
| < 45 | `NO_TRADE` |
| 45 – 54 | `WATCH` |
| 55 – buy threshold | `WEAK_SETUP` |
| ≥ buy threshold | `BUY_CANDIDATE` |
| ≥ buy threshold + 10 | `STRONG_BUY_CANDIDATE` |

The buy threshold depends on the regime: BULL 65, NEUTRAL 70, HIGH_VOLATILITY 75, BEAR 80
(`strategy.buy_threshold`; lowered in Oct 2026 from 75/80/85/88). `--alim-esigi N` sets the
BULL threshold to N for one run and shifts the others by the same amount. A buy-level score is still downgraded to `WEAK_SETUP` unless it
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

## Backtesting (Phase 2)

`config/backtest.yaml` holds the period, universe, costs, exit rules and walk-forward grid.
Any setting can be overridden per run with `--set dotted.path=value`.

**Timing.** Signals use data up to the close of session T. Orders fill during T+1:
- `zone_limit` (default): a buy limit at close + 0.25 ATR. It fills at the open if the open is at or below the limit, otherwise at the limit if the low reaches it; if neither, the order expires.
- `next_open`: a market buy at the open.

A test checks that trades closed before a cut-off date are identical whether or not later data exists.

**Exits**, checked in this order each session:
1. An exit decided at the previous close executes at the open.
2. A gap through the stop or a target at the open fills at the open price.
3. Intraday stop, then TP1 (sell 50%, move the stop to breakeven), then TP2. When a bar touches both the stop and a target, the stop is assumed to fill first.
4. At the close: a trend-failure exit (close < EMA20 and MACD < signal), a time stop (no +0.5R after 10 sessions), or the 20-session maximum hold is decided and executed at the next open.

An optional ATR trailing stop is also available.

**Costs.** Every fill pays commission (0.10%), exchange fee (0.005%) and slippage (0.10%) per side, all configurable.

**Portfolio.** 1% risk per trade on current equity. Limits: at most 8 positions, 20% of equity per position, 5% total capital at risk, 25% per sector. No leverage. Idle cash earns `cash_interest_annual_pct` (default 0).

**Reports.**
- **`backtest`:** all spec metrics (trades, win/loss, average winner/loser, profit factor, expectancy in R and TRY, max drawdown and duration, Sharpe, Sortino, CAGR, exposure, holding period, best/worst trade, recovery factor, costs). Also exit-reason and regime breakdowns, year-by-year returns, and XU100 / XU030 / equal-weight benchmarks. `--trades` and `--equity` export CSVs.
- **`sweep`:** the same period for every parameter combination, as a robustness check.
- **`walkforward`:** rolling 3-year train / 1-year test windows. The best parameter set in-sample (by Sharpe, with a minimum trade count) is tested on the next unseen year, and the out-of-sample years are chained together.
- **`research`:** forward returns (next open to close at +1/3/5/10/20 sessions) by score bucket, signal and regime, raw and in excess of the universe average, plus a daily rank information coefficient with a t-stat. No costs are applied here, so this measures predictive content only.

Everything runs through the same `strategy/evaluate.py` code the scanner uses.

## News & KAP (Phase 3)

Company disclosures come from **KAP** (kap.org.tr). The client uses the JSON endpoints
behind the site's public disclosure search. That is not a documented API, so it may
change. How it works:
- **Resolution:** each ticker is resolved to its KAP company id; some companies list several codes.
- **Download:** disclosures are fetched per month for the universe, and any query that hits the 2,000-row cap is split automatically.
- **Storage:** everything is stored raw under `data/raw/kap/`, and syncs are incremental.
- **Full text:** the disclosure text is downloaded only for event types where amounts matter.

**Classification** produces metadata only (event type, sentiment −1..1, importance,
confidence, impact horizon, amount); it never produces a trade decision. Implementations of the `NewsClassifier`
protocol:

| `news.classifier` | What | Cost |
|---|---|---|
| `rules` (default) | Deterministic Turkish rules on KAP's subject and summary: contracts, bonus / rights issues, buybacks, dividends, credit ratings, exchange measures (brüt takas, tek fiyat…), SPK bans, share conversions, production issues, lawsuits, M&A, investments… | free, reproducible, used for backtests |
| `claude` | Every disclosure classified by Claude (`claude-opus-5`, effort `low`, JSON-schema output, refusal fallback). Responses are cached on disk per disclosure | API cost; needs `pip install -e ".[llm]"` and `ANTHROPIC_API_KEY` |
| `hybrid` | Rules for routine filings, Claude only for potentially material ones | reduced API cost |

**News score (0–15)**, computed point-in-time:
- **Net impact** is the sum over recent events of sentiment × importance × confidence × source quality × size × reaction × decay.
- **Points** are 7.5 × (1 + tanh(net / 0.6)): no news gives a neutral 7.5, and strongly positive or negative news pushes toward 15 or 0.
- **Decay** is exponential, with half-lives of 1, 3 and 7 days for short, medium and long impact horizons, and nothing counts after 10 days. The spec's step table is also available.
- **Size** (spec §23) compares the stated amount (TRY / USD / EUR) with the market cap on the event date (İş Yatırım data). An amount of 1% of market cap is neutral; the multiplier ranges from 0.5 to 2.
- **Reaction** (spec §24) measures the excess move versus XU100 in the event session, in ATR units. It is only used once it could have been observed, at that session's close. The rule is data-driven: it keeps only the effects that held in both 2016–20 and 2021–26. Good news that sells off counts as weakness (×−0.5). A negative reaction to neutral material news, such as a financial report, is read as negative sentiment. The spec's other hypotheses were not supported and are left neutral: "good news + no reaction = weakness" and "bad news + no fall = strength" (see `docs/phase3_findings.md`).
- **Sentiment overrides** neutralise event types whose effect flipped sign between the two periods: bonus issues, insider transactions, investments, dividends and M&A (`news.sentiment_overrides`).
- **Cutoff:** a disclosure counts for day T's signal only if it was published by T 18:15. After-hours disclosures count from the next session.

`research` adds a **KAP event study**: excess returns versus XU100 after each event type and tone, and after each news-vs-reaction pattern. `--no-news` (or `--set news.enabled=false`) gives the technical-only baseline for ablation.

## Configuration

| File | Contents |
|---|---|
| `config/settings.yaml` | data provider and cache, indicator periods, regime rules, thresholds, risk, liquidity, news (classifier, decay, reaction, scale, LLM) |
| `config/scoring.yaml` | factor weights (must sum to 100), rubric thresholds, bands |
| `config/universe.yaml` | mode (`BIST30`, `BIST50`, `BIST100`, `CUSTOM`), symbol lists, sector map |
| `config/backtest.yaml` | backtest period, costs, entry mode, exits, walk-forward grid, research horizons |
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
- **KAP and İş Yatırım** are accessed through their websites' internal JSON endpoints. These are unofficial and may change; a failure disables the news factor with a warning instead of stopping the scan.
- **Financial news beyond KAP** (RSS or news APIs) is not ingested yet. Without a historical archive it could not be backtested anyway.
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
  data/                 market_data.py (providers), quality.py, universe.py,
                        kap_data.py (KAP client/store), fundamentals.py (market cap, FX)
  news/                 parser, classifier (rules/hybrid), llm (Claude), decay, scale,
                        reaction, pipeline
  indicators/           trend, momentum, volatility, volume, relative_strength, features
  regime/               market_regime.py
  scoring/              technical_score.py, news_score.py, composite_score.py
  strategy/             entry.py (classification), filters.py (liquidity, stale data)
  risk/                 stop_loss, take_profit, position_size, plan
  strategy/evaluate.py  one row -> score -> signal (shared by scanner and backtest)
  backtest/             data, signals, engine, metrics, runner (sweep), walk_forward,
                        research, report
tests/unit, tests/integration
data/raw, data/processed, data/cache   (git-ignored)
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Technical scanner, regime, scoring, risk plan, CLI, tests | ✅ this repo |
| 2 | Backtesting: costs, stops/targets, metrics, benchmarks, walk-forward, factor research | ✅ |
| 3 | News & KAP ingestion, LLM classification (metadata only), decay, news score, event study | ✅ |
| 4 | Broker/institutional flow (incl. BofA), custody data, flow score | next |
| 5 | Composite model, ablation, out-of-sample validation | planned |
| 6–8 | Dashboard, paper trading, optional broker execution | planned |

`score_components(..., extra={"news": ..., "institutional_flow": ...})` is the hook through
which Phase 3 and 4 scores plug in without touching the technical code.
