# BIST Quant Scanner & Trading Signal Bot

> **Purpose:** Build a production-oriented Borsa İstanbul (BIST) market analysis and trading-signal platform that combines technical indicators, market regime, financial/news sentiment, KAP disclosures, institutional flow (including Bank of America / BOFA-style broker flow when data is available), and risk management into a unified scoring engine.

---

# 1. Project Summary

This project will scan selected Borsa İstanbul equities, primarily BIST 30 / BIST 100 stocks, and produce systematic short-to-medium-term trading signals.

The first target horizon is:

- **Primary strategy horizon:** 3–20 trading days
- **Style:** Swing trading
- **Initial execution mode:** Signal generation only
- **Later execution modes:** Paper trading, then optionally broker-integrated live execution

The system must **not** rely on a single indicator such as RSI or MACD.

Instead, every stock will be evaluated using multiple independent information layers:

1. Technical trend
2. Momentum
3. Volume
4. Volatility
5. Relative strength
6. Market regime
7. Financial/news sentiment
8. KAP/company events
9. Institutional / broker money flow
10. Risk/reward conditions

The output should be a transparent, explainable score and trading decision.

Example:

```text
THYAO

Technical Trend       18/20
Momentum              14/15
Volume                 8/10
Relative Strength      8/10
News / KAP            12/15
Institutional Flow    13/15
Market Regime          9/10
Volatility             4/5

TOTAL SCORE:          86/100

Signal: BUY CANDIDATE

Entry Zone:  xxx.xx - xxx.xx
Stop Loss:   xxx.xx
Take Profit: xxx.xx
Risk/Reward: 2.6

Reasons:
- Price above EMA50 and EMA200
- MACD bullish
- RSI healthy
- Volume above average
- Positive company disclosure
- Institutional accumulation detected
```

---

# 2. Main Goals

The system should answer five questions for every stock:

1. **Is the stock technically strong?**
2. **Is momentum supporting the trend?**
3. **Is real volume / institutional money entering or exiting?**
4. **Are current news and company events supportive or harmful?**
5. **Is the potential reward worth the risk?**

The bot should not simply say:

```text
RSI < 30 => BUY
```

Instead, it should combine multiple factors and produce a weighted score.

---

# 3. Non-Goals for V1

Do NOT build these in the first version:

- High-frequency trading
- Millisecond-level execution
- Fully autonomous live order execution
- Reinforcement learning
- Deep learning price prediction
- Options/futures strategies
- Intraday scalping
- Social-media-only signals
- Portfolio leverage
- Short selling

V1 should be robust, explainable, testable, and simple enough to validate statistically.

---

# 4. Suggested Technology Stack

## Core

```text
Python 3.12+
pandas
numpy
polars (optional)
scipy
scikit-learn
pydantic
SQLAlchemy
```

## Technical Indicators

Preferred:

```text
pandas-ta
```

Alternative:

```text
TA-Lib
```

## Backtesting

Possible choices:

```text
vectorbt
backtesting.py
backtrader
```

Recommended first choice:

```text
vectorbt
```

because large multi-symbol backtests can be run efficiently.

## Database

Initial:

```text
PostgreSQL
```

Optional time-series extension:

```text
TimescaleDB
```

Local development:

```text
SQLite
```

## API

```text
FastAPI
```

## Scheduler

Simple:

```text
APScheduler
```

Production option:

```text
Celery + Redis
```

## Dashboard

V1:

```text
Streamlit
```

Later:

```text
React / Next.js + FastAPI
```

## AI / NLP

Use an LLM only for:

- KAP document classification
- News classification
- Event extraction
- Sentiment analysis
- Impact estimation

The LLM must **not directly produce BUY/SELL orders**.

---

# 5. Market Universe

Initial universe:

```text
BIST 30
```

Then expand to:

```text
BIST 50
BIST 100
```

The system should support a configurable stock universe.

Example:

```yaml
universe:
  mode: BIST100
```

or:

```yaml
symbols:
  - THYAO
  - ASELS
  - TUPRS
  - BIMAS
  - KCHOL
```

---

# 6. Trading Horizon

Primary design target:

```text
3–20 trading days
```

This is intentionally not an intraday trading system.

Reason:

- Technical indicators are more stable on daily candles.
- Transaction costs matter less than in scalping.
- Institutional flows and KAP/news effects can persist for several sessions.
- Market regime filters become more useful.

Possible later strategy profiles:

```text
SHORT_SWING: 1–5 days
SWING:       3–20 days
POSITION:    2–12 weeks
```

V1 should focus only on:

```text
SWING
```

---

# 7. System Architecture

```text
                         +----------------------+
                         |    Market Data       |
                         | OHLCV / Index Data   |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         | Technical Engine     |
                         | RSI/MACD/EMA/BB/ATR  |
                         +----------+-----------+
                                    |
          +-------------------------+--------------------------+
          |                         |                          |
          v                         v                          v
+------------------+     +---------------------+    +----------------------+
| Market Regime    |     | News / KAP Engine   |    | Institutional Flow   |
| XU100 / XU030    |     | NLP / Event Parser  |    | Broker / Custody     |
+--------+---------+     +----------+----------+    +----------+-----------+
         |                          |                          |
         +--------------------------+--------------------------+
                                    |
                                    v
                         +----------------------+
                         |   Scoring Engine     |
                         |       0–100          |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         | Risk Management      |
                         | ATR / R:R / Position |
                         +----------+-----------+
                                    |
                    +---------------+---------------+
                    |                               |
                    v                               v
         +----------------------+       +----------------------+
         | Backtest / Research  |       | Live Scanner         |
         +----------------------+       +----------+-----------+
                                                  |
                                                  v
                                       +----------------------+
                                       | Paper Trading        |
                                       +----------+-----------+
                                                  |
                                                  v
                                       +----------------------+
                                       | Optional Live Orders |
                                       +----------------------+
```

---

# 8. Core Modules

The application should be split into independent modules.

Recommended repository structure:

```text
bist-quant-bot/
│
├── README.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
│
├── config/
│   ├── settings.yaml
│   ├── scoring.yaml
│   └── universe.yaml
│
├── src/
│   └── bist_quant/
│       │
│       ├── main.py
│       │
│       ├── config.py
│       │
│       ├── logging.py
│       │
│       ├── models/
│       │   ├── market.py
│       │   ├── signals.py
│       │   ├── news.py
│       │   ├── institutional_flow.py
│       │   └── trades.py
│       │
│       ├── data/
│       │   ├── market_data.py
│       │   ├── index_data.py
│       │   ├── kap_data.py
│       │   ├── news_data.py
│       │   ├── broker_flow.py
│       │   └── repository.py
│       │
│       ├── indicators/
│       │   ├── trend.py
│       │   ├── momentum.py
│       │   ├── volatility.py
│       │   ├── volume.py
│       │   └── relative_strength.py
│       │
│       ├── regime/
│       │   └── market_regime.py
│       │
│       ├── news/
│       │   ├── parser.py
│       │   ├── classifier.py
│       │   └── decay.py
│       │
│       ├── flow/
│       │   ├── institutional_flow.py
│       │   ├── broker_scoring.py
│       │   └── custody_analysis.py
│       │
│       ├── scoring/
│       │   ├── technical_score.py
│       │   ├── news_score.py
│       │   ├── flow_score.py
│       │   └── composite_score.py
│       │
│       ├── strategy/
│       │   ├── entry.py
│       │   ├── exit.py
│       │   └── filters.py
│       │
│       ├── risk/
│       │   ├── stop_loss.py
│       │   ├── take_profit.py
│       │   ├── position_size.py
│       │   └── portfolio_risk.py
│       │
│       ├── backtest/
│       │   ├── engine.py
│       │   ├── metrics.py
│       │   └── walk_forward.py
│       │
│       ├── paper/
│       │   └── paper_broker.py
│       │
│       ├── api/
│       │   ├── app.py
│       │   └── routes/
│       │
│       └── dashboard/
│           └── streamlit_app.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── backtest/
│
├── notebooks/
│   ├── research.ipynb
│   └── factor_analysis.ipynb
│
└── data/
    ├── raw/
    ├── processed/
    └── cache/
```

---

# 9. Market Data

Minimum daily OHLCV structure:

```python
symbol: str
date: datetime
open: float
high: float
low: float
close: float
volume: float
adjusted_close: float | None
```

Required timeframe:

```text
1D
```

Later:

```text
1H
4H
```

Historical target:

```text
Minimum: 5 years
Preferred: 8–10 years
```

The system must handle:

- Splits
- Bonus issues
- Dividends
- Symbol changes
- Suspended securities
- Missing candles

All backtests must use adjusted prices where appropriate.

---

# 10. Technical Indicator Engine

## 10.1 Trend Indicators

Calculate:

```text
EMA20
EMA50
EMA100
EMA200
```

Primary trend conditions:

```text
Price > EMA20
Price > EMA50
Price > EMA200

EMA20 > EMA50
EMA50 > EMA200
```

Example bullish structure:

```text
Close > EMA20 > EMA50 > EMA200
```

---

# 11. RSI

Default:

```text
RSI(14)
```

Do NOT use only:

```text
RSI < 30 => BUY
```

Preferred interpretation:

```text
RSI < 30      Oversold
30–45         Weak
45–65         Healthy momentum
65–70         Strong
>70           Potentially overextended
```

Potential bullish trigger:

```text
RSI crosses above 45
```

or:

```text
RSI crosses above 50
```

The exact threshold must be tested rather than assumed.

---

# 12. MACD

Default:

```text
MACD 12 / 26 / 9
```

Track:

```text
MACD line
Signal line
Histogram
```

Bullish factors:

```text
MACD > Signal
Histogram > previous histogram
Histogram crosses above zero
```

Bearish factors:

```text
MACD < Signal
Histogram falling
Histogram crosses below zero
```

---

# 13. Bollinger Bands

Default:

```text
Period: 20
StdDev: 2
```

Uses:

- Breakout detection
- Mean reversion research
- Volatility compression
- Squeeze detection

Potential breakout condition:

```text
Close > Upper Bollinger Band
AND
Volume > VolumeMA20 * 1.20
```

This should not automatically trigger a BUY.

It contributes to the score.

---

# 14. ATR

Default:

```text
ATR(14)
```

ATR is primarily used for risk management.

Example stop:

```text
Stop Loss = Entry - 2 * ATR
```

Possible configurable multiplier:

```yaml
risk:
  atr_stop_multiplier: 2.0
```

---

# 15. ADX

Use:

```text
ADX(14)
```

Purpose:

Identify trend strength.

Example:

```text
ADX < 20  => weak / sideways market
ADX 20–25 => emerging trend
ADX > 25  => stronger trend
```

ADX direction alone should not generate BUY/SELL signals.

---

# 16. Volume Analysis

Calculate:

```text
VolumeMA20
VolumeRatio
```

Formula:

```text
VolumeRatio = CurrentVolume / AverageVolume20
```

Potential conditions:

```text
VolumeRatio > 1.20
VolumeRatio > 1.50
VolumeRatio > 2.00
```

Volume spikes should be combined with price direction.

Example:

```text
Price breakout + high volume => bullish confirmation

Price drop + high volume => bearish distribution risk
```

---

# 17. Relative Strength

A stock should not only be evaluated in isolation.

Compare performance against:

```text
XU100
XU030
sector index
```

Example:

```text
Stock 20D Return - XU100 20D Return
```

Calculate:

```text
RS_5D
RS_20D
RS_60D
```

Strong stocks should ideally outperform the market.

---

# 18. Market Regime Engine

The bot must detect the general market environment.

Primary index:

```text
XU100
```

Optional secondary:

```text
XU030
```

Possible regimes:

```text
BULL
NEUTRAL
BEAR
HIGH_VOLATILITY
```

Example logic:

### Bull

```text
XU100 > EMA50
EMA50 > EMA200
MACD positive
```

### Bear

```text
XU100 < EMA50
EMA50 < EMA200
```

### High Volatility

Use:

```text
ATR percentile
historical volatility
large index drawdown
```

Market regime should modify signal thresholds.

Example:

```text
BULL market:
BUY threshold = 75

NEUTRAL:
BUY threshold = 80

BEAR:
BUY threshold = 88
```

This reduces aggressive long exposure in bad markets.

---

# 19. News & KAP Engine

Sources may include:

- KAP disclosures
- Company investor relations releases
- Financial news APIs
- Major financial news providers
- Economic calendar
- Macro releases

The LLM should not decide whether to trade.

It should return structured metadata.

Example schema:

```json
{
  "symbol": "ASELS",
  "event_type": "new_contract",
  "sentiment": 0.82,
  "importance": 0.74,
  "confidence": 0.91,
  "impact_horizon": "medium",
  "summary": "Company announced a new material contract."
}
```

---

# 20. News Event Types

Support at least:

```text
new_contract
earnings
guidance
dividend
capital_increase
share_buyback
merger_acquisition
investment
capacity_expansion
production_issue
regulatory_action
lawsuit
management_change
credit_rating
debt_refinancing
export_deal
government_contract
macro_event
sector_event
other
```

---

# 21. News Sentiment

Normalize to:

```text
-1.0 ... +1.0
```

Where:

```text
-1.0 = strongly negative
 0.0 = neutral
+1.0 = strongly positive
```

Also calculate:

```text
importance
confidence
source_quality
```

---

# 22. News Decay

News impact should decay over time.

Example default:

```text
0–6 hours      100%
6–24 hours      70%
1–3 days        40%
3–7 days        15%
7+ days          0%
```

A configurable exponential decay function is preferable:

```python
effective_news_score =
    raw_score
    * source_quality
    * confidence
    * exp(-lambda * age_hours)
```

---

# 23. Company-Scale Adjustment

The same event must not have equal impact on all companies.

Example:

```text
New contract value = 500M TRY
```

The impact should consider:

```text
contract_value / annual_revenue
contract_value / market_cap
contract_value / EBITDA
```

Example:

```python
contract_revenue_ratio = contract_value / annual_revenue
```

This can modify event importance.

---

# 24. News vs Price Reaction

One important factor:

```text
Good news + stock does not rise
```

may indicate weakness.

Similarly:

```text
Bad news + stock does not fall
```

may indicate strength.

Create:

```text
news_price_divergence_score
```

Example classifications:

```text
POSITIVE_NEWS_POSITIVE_REACTION
POSITIVE_NEWS_NO_REACTION
POSITIVE_NEWS_NEGATIVE_REACTION
NEGATIVE_NEWS_NEGATIVE_REACTION
NEGATIVE_NEWS_NO_REACTION
NEGATIVE_NEWS_POSITIVE_REACTION
```

---

# 25. Institutional / Broker Flow

Institutional flow is a major optional factor.

Potential tracked brokerages:

```text
Bank of America
Yapı Kredi Yatırım
İş Yatırım
Ak Yatırım
Garanti BBVA Yatırım
QNB
İnfo Yatırım
Tacirler
other major brokers
```

Important:

**Broker transaction distribution is not necessarily equivalent to the brokerage's proprietary position.**

The system must treat this as:

```text
broker-flow signal
```

not:

```text
"BOFA portfolio position"
```

---

# 26. BOFA / Broker Flow Features

For each symbol calculate:

```text
1D net buy/sell
3D cumulative net flow
5D cumulative net flow
10D cumulative net flow
flow persistence
flow acceleration
flow / market turnover
flow / stock average daily turnover
price reaction
```

Key normalization:

```python
normalized_flow =
    broker_net_flow / stock_average_daily_turnover_20d
```

This is more useful than absolute TRY value.

---

# 27. Flow Persistence

Example:

```text
Day 1 +180M
Day 2 +240M
Day 3 +310M
Day 4 +420M
Day 5 +390M
```

This is much stronger than:

```text
+400M
-350M
+300M
-400M
```

Possible persistence metric:

```python
positive_days_ratio =
    positive_flow_days / lookback_days
```

Other useful metrics:

```text
net_flow_sum
net_flow_mean
net_flow_std
positive_day_ratio
flow_trend_slope
```

---

# 28. Flow / Price Divergence

Detect:

### Accumulation candidate

```text
institutional buying
+
price sideways
+
selling pressure decreasing
```

### Distribution candidate

```text
price rising
+
institutional selling
+
multiple large brokers selling
```

### Absorption

```text
strong broker selling
+
price not falling
```

This may indicate hidden demand.

---

# 29. Custody / Takas Data

Broker transactions and custody data should be treated separately.

When custody data is available:

Track:

```text
1D custody change
5D custody change
20D custody change
foreign ownership change
major institution changes
```

If:

```text
broker buying
+
custody accumulation
+
price confirmation
```

then institutional-flow confidence increases.

---

# 30. Scoring Model

Initial 100-point model:

| Factor | Weight |
|---|---:|
| Technical Trend | 20 |
| Momentum | 15 |
| Volume | 10 |
| Relative Strength | 10 |
| News / KAP | 15 |
| Institutional Flow | 15 |
| Market Regime | 10 |
| Volatility / Setup Quality | 5 |
| **Total** | **100** |

Configurable:

```yaml
weights:
  trend: 20
  momentum: 15
  volume: 10
  relative_strength: 10
  news: 15
  institutional_flow: 15
  market_regime: 10
  volatility: 5
```

---

# 31. Example Technical Score

Possible scoring:

```text
Close > EMA20                    +2
Close > EMA50                    +3
Close > EMA200                   +3
EMA20 > EMA50                    +2
EMA50 > EMA200                   +3
ADX > 25                         +2
trend persistence               +5
```

Normalize to:

```text
0–20
```

---

# 32. Momentum Score

Possible inputs:

```text
RSI
MACD crossover
MACD histogram
ROC
momentum persistence
```

Normalize:

```text
0–15
```

---

# 33. Volume Score

Possible inputs:

```text
VolumeRatio
OBV
Accumulation/Distribution
breakout volume
volume trend
```

Normalize:

```text
0–10
```

---

# 34. Relative Strength Score

Inputs:

```text
5D vs XU100
20D vs XU100
60D vs XU100
sector relative strength
```

Normalize:

```text
0–10
```

---

# 35. News Score

Possible components:

```text
sentiment
importance
confidence
source_quality
event_scale
time_decay
price_confirmation
```

Normalize:

```text
0–15
```

---

# 36. Institutional Flow Score

Possible components:

```text
1D flow                         0–2
3D flow                         0–3
5D flow                         0–3
persistence                     0–2
flow / turnover                 0–2
price confirmation              0–2
custody confirmation            0–1
```

Maximum:

```text
15
```

---

# 37. Market Regime Score

Possible:

```text
XU100 > EMA20
XU100 > EMA50
XU100 > EMA200
EMA50 > EMA200
MACD positive
market breadth healthy
```

Normalize:

```text
0–10
```

---

# 38. Final Signal Categories

Example initial thresholds:

```text
0–49   NO TRADE
50–64  WATCH
65–74  WEAK SETUP
75–84  BUY CANDIDATE
85–100 STRONG BUY CANDIDATE
```

Important:

These are **strategy classifications**, not promises of future return.

Thresholds must be validated by backtesting.

---

# 39. Entry Logic

A BUY candidate should require more than score alone.

Example:

```python
if (
    total_score >= buy_threshold
    and risk_reward_ratio >= 2.0
    and market_regime != "BEAR"
    and liquidity_filter_passed
):
    signal = "BUY_CANDIDATE"
```

---

# 40. Liquidity Filter

Avoid illiquid stocks.

Example:

```text
AverageDailyTurnover20D > configurable threshold
```

Possible additional filters:

```text
minimum trade count
minimum daily volume
maximum bid/ask spread
minimum market cap
```

---

# 41. Risk Management

Risk management is mandatory.

Do not generate trade entries without:

```text
entry
stop
take-profit
risk/reward
position size
```

---

# 42. Stop Loss

Default:

```python
stop = entry - ATR(14) * atr_multiplier
```

Example:

```text
Entry = 100
ATR = 3
Multiplier = 2

Stop = 94
```

---

# 43. Take Profit

Prefer R-based exits.

If:

```text
risk = entry - stop
```

then:

```text
TP1 = entry + 1.5R
TP2 = entry + 2.5R
```

Configurable.

---

# 44. Trailing Stop

Optional:

```text
ATR trailing stop
```

Example:

```python
trailing_stop = highest_close_since_entry - 2 * ATR
```

---

# 45. Position Sizing

Risk a fixed percentage of portfolio equity.

Example:

```text
Portfolio = 500,000 TRY
Risk per trade = 1%
Allowed loss = 5,000 TRY
```

If:

```text
Entry = 100
Stop = 94
Risk per share = 6 TRY
```

then:

```python
shares = 5000 / 6
```

Rounded down to valid lot size.

Config:

```yaml
risk:
  risk_per_trade_pct: 1.0
  max_open_positions: 8
  max_sector_exposure_pct: 25
```

---

# 46. Portfolio Risk

Future portfolio rules:

```text
max total open risk
max positions
max exposure per stock
max exposure per sector
max correlated positions
```

Example:

```text
Maximum portfolio open risk = 5%
```

---

# 47. Sell / Exit Logic

Exit can happen due to:

### Hard risk exit

```text
Stop loss hit
```

### Profit target

```text
TP1
TP2
```

### Trend failure

```text
Close < EMA20
MACD bearish
RSI breaks below threshold
```

### Institutional reversal

```text
strong multi-day institutional selling
```

### News shock

```text
material negative KAP / event
```

### Time stop

Example:

```text
No meaningful progress after 10 trading days
```

---

# 48. Backtesting

Backtesting is essential.

The system must calculate:

```text
Total trades
Win rate
Loss rate
Average winner
Average loser
Profit factor
Expectancy
Maximum drawdown
Sharpe ratio
Sortino ratio
CAGR
Exposure
Average holding period
Best trade
Worst trade
Recovery factor
```

---

# 49. Benchmark

Compare strategy to:

```text
XU100 buy & hold
XU030 buy & hold
equal-weight stock universe
```

---

# 50. Transaction Costs

Backtest must include:

```text
broker commission
exchange fees
bid/ask spread estimate
slippage
```

Never evaluate a strategy without costs.

Example:

```yaml
backtest:
  commission_pct: 0.001
  slippage_pct: 0.001
```

Values are placeholders and should be configured using the actual broker conditions.

---

# 51. Look-Ahead Bias Prevention

No strategy code may use future data.

Bad:

```python
today_signal = future_price > today_price
```

Good:

```python
today_signal = features_known_at_close_today
```

If trades are executed next session:

```text
Signal on T close
Execution on T+1 open
```

This should be explicit.

---

# 52. Survivorship Bias

Backtest should eventually account for historical BIST membership.

Do not assume today's BIST100 constituents existed throughout historical periods.

For V1 this limitation may be documented.

For serious validation, use historical index membership.

---

# 53. Overfitting Prevention

Do not over-optimize:

```text
RSI = 47.35
EMA = 37
Volume = 1.283x
```

Prefer robust parameter regions.

Example testing:

```text
RSI threshold:
45
47.5
50
52.5
55
```

If only one exact value works, the strategy is suspicious.

---

# 54. Train / Validation / Test

Example chronological split:

```text
2017–2022 => research / train
2023–2024 => validation
2025–2026 => out-of-sample test
```

Never randomly shuffle financial time series.

---

# 55. Walk-Forward Testing

Implement:

```text
Train Window
      ↓
Validation
      ↓
Move forward
      ↓
Repeat
```

This is more realistic than one static backtest.

---

# 56. Factor Research

Before combining everything, test every factor independently.

Example research questions:

```text
What happens after RSI crosses 50?

What is the average 5D return after MACD bullish crossover?

What happens after volume > 2x average?

What happens after BOFA net buying > 20% of average daily turnover?

What happens after positive KAP news?

What happens when BOFA buying + positive MACD occur together?
```

Forward return windows:

```text
1D
3D
5D
10D
20D
```

---

# 57. Institutional Flow Research

A key research task:

For each large brokerage:

```text
When net flow is strongly positive:
calculate forward returns at
1D / 3D / 5D / 10D / 20D
```

Then test combinations:

```text
BOFA buying + MACD bullish
BOFA buying + volume breakout
BOFA buying + positive news
BOFA buying + custody increase
BOFA buying + XU100 bull regime
```

This determines whether institutional flow adds predictive value.

---

# 58. Feature Storage

Store calculated features.

Example database table:

```text
daily_features
```

Columns:

```text
symbol
date
close
ema20
ema50
ema200
rsi14
macd
macd_signal
macd_hist
bb_upper
bb_lower
atr14
adx14
volume_ratio
rs_5d
rs_20d
rs_60d
market_regime
news_score
institutional_flow_score
total_score
```

---

# 59. Signals Table

```text
signals
```

Example fields:

```text
id
symbol
date
signal_type
score
entry_price
stop_price
tp1
tp2
risk_reward
market_regime
explanation_json
created_at
```

---

# 60. News Table

```text
news_events
```

Fields:

```text
id
symbol
published_at
source
headline
event_type
sentiment
importance
confidence
source_quality
impact_horizon
raw_text
llm_response_json
```

---

# 61. Institutional Flow Table

```text
institutional_flow
```

Fields:

```text
symbol
date
broker
buy_value
sell_value
net_value
market_turnover
stock_turnover
normalized_flow
rolling_3d_flow
rolling_5d_flow
rolling_10d_flow
persistence_score
```

---

# 62. API Endpoints

Suggested FastAPI endpoints.

## Symbols

```http
GET /symbols
```

## Latest signals

```http
GET /signals
```

## Symbol analysis

```http
GET /analysis/{symbol}
```

Example response:

```json
{
  "symbol": "THYAO",
  "score": 86,
  "signal": "BUY_CANDIDATE",
  "entry": 0,
  "stop": 0,
  "tp1": 0,
  "tp2": 0,
  "risk_reward": 2.6,
  "components": {
    "trend": 18,
    "momentum": 14,
    "volume": 8,
    "relative_strength": 8,
    "news": 12,
    "institutional_flow": 13,
    "market_regime": 9,
    "volatility": 4
  }
}
```

## Backtest

```http
POST /backtest
```

## News

```http
GET /news/{symbol}
```

## Institutional flow

```http
GET /flow/{symbol}
```

---

# 63. Scanner Output

CLI example:

```text
BIST QUANT SCANNER
Date: 2026-XX-XX

1. THYAO  86  BUY_CANDIDATE
2. ASELS  82  BUY_CANDIDATE
3. TUPRS  78  BUY_CANDIDATE
4. BIMAS  72  WEAK_SETUP
5. KCHOL  64  WATCH
```

Detailed:

```text
THYAO

Score: 86/100
Signal: BUY_CANDIDATE

Trend:               18/20
Momentum:            14/15
Volume:               8/10
Relative Strength:    8/10
News:                12/15
Institutional Flow:  13/15
Market Regime:        9/10
Volatility:           4/5

Entry:  xxx.xx
Stop:   xxx.xx
TP1:    xxx.xx
TP2:    xxx.xx

Risk/Reward: 2.6

Reasons:
+ Price > EMA50
+ EMA50 > EMA200
+ MACD bullish
+ RSI = 58
+ Volume = 1.6x average
+ Positive KAP event
+ 5D institutional accumulation
```

---

# 64. Explanation Engine

Every signal must be explainable.

Example:

```json
{
  "positive_factors": [
    "Price above EMA50",
    "EMA50 above EMA200",
    "MACD bullish",
    "Volume 1.6x 20D average",
    "5D broker flow positive"
  ],
  "negative_factors": [
    "RSI near overbought threshold"
  ]
}
```

The system must never return only:

```text
BUY
```

without reasons.

---

# 65. Configuration

Example:

```yaml
strategy:
  profile: SWING
  buy_threshold: 75
  strong_buy_threshold: 85

indicators:
  rsi_period: 14
  ema_fast: 20
  ema_medium: 50
  ema_slow: 200
  atr_period: 14
  adx_period: 14
  volume_ma_period: 20

risk:
  atr_stop_multiplier: 2.0
  tp1_r: 1.5
  tp2_r: 2.5
  risk_per_trade_pct: 1.0
  minimum_rr: 2.0

market_regime:
  index: XU100

news:
  enabled: true

institutional_flow:
  enabled: true
  lookbacks:
    - 1
    - 3
    - 5
    - 10
```

---

# 66. Environment Variables

Example `.env.example`:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/bist_quant

MARKET_DATA_API_KEY=
NEWS_API_KEY=
KAP_API_KEY=

OPENAI_API_KEY=
ANTHROPIC_API_KEY=

BROKER_FLOW_API_KEY=
CUSTODY_DATA_API_KEY=

LOG_LEVEL=INFO
```

Never commit real credentials.

---

# 67. Data Provider Abstraction

Do not tightly couple the project to one vendor.

Define interfaces.

Example:

```python
class MarketDataProvider(Protocol):
    async def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date
    ) -> DataFrame:
        ...
```

Similarly:

```text
NewsProvider
KapProvider
BrokerFlowProvider
CustodyProvider
BrokerExecutionProvider
```

This allows changing vendors later.

---

# 68. LLM Abstraction

Support multiple providers.

Example:

```python
class NewsClassifier(Protocol):
    async def classify(self, article: NewsArticle) -> NewsClassification:
        ...
```

Possible implementations:

```text
OpenAIClassifier
ClaudeClassifier
LocalModelClassifier
```

The trading logic must not depend directly on one LLM vendor.

---

# 69. Pydantic Models

Example:

```python
class NewsClassification(BaseModel):
    symbol: str | None
    event_type: str
    sentiment: float
    importance: float
    confidence: float
    source_quality: float
    impact_horizon: str
    summary: str
```

Validation:

```text
sentiment       -1.0 .. 1.0
importance       0.0 .. 1.0
confidence       0.0 .. 1.0
source_quality   0.0 .. 1.0
```

---

# 70. Paper Trading

Before live execution, run a paper trading phase.

The paper broker should simulate:

```text
orders
fills
commission
slippage
portfolio cash
positions
realized PnL
unrealized PnL
```

Suggested minimum evaluation period:

```text
60–90 trading days
```

or enough trades to establish meaningful behavior.

---

# 71. Live Execution

Live broker integration should only be implemented after:

```text
backtest passes
+
out-of-sample test passes
+
paper trading passes
```

Execution should be a separate module.

Never let signal generation directly execute orders.

Architecture:

```text
Signal Engine
     |
     v
Execution Approval Layer
     |
     v
Broker Adapter
```

This allows:

```text
manual approval
paper approval
automatic approval
```

---

# 72. Safety Controls

Future live system should include:

```text
daily loss limit
max order size
max position size
max open positions
kill switch
duplicate order protection
stale-data protection
API failure handling
market-hours validation
```

Example:

```text
If market data age > threshold:
DO NOT TRADE
```

---

# 73. Logging

Log:

```text
data updates
indicator calculations
news classification
flow calculations
score generation
signal changes
paper orders
live orders
exceptions
API errors
```

Use structured logs.

Example:

```json
{
  "event": "signal_generated",
  "symbol": "THYAO",
  "score": 86,
  "signal": "BUY_CANDIDATE"
}
```

---

# 74. Testing Requirements

Unit tests:

```text
indicator calculations
score boundaries
news decay
flow normalization
ATR stops
position sizing
market regime classification
```

Integration tests:

```text
provider -> database
database -> scoring engine
scanner -> API
paper broker
```

Backtest regression tests:

A known dataset should produce stable expected metrics.

---

# 75. Development Phases

## Phase 1 — Core Technical Scanner

Implement:

```text
BIST symbols
daily OHLCV
EMA
RSI
MACD
Bollinger
ATR
ADX
Volume
Relative Strength
XU100 regime
basic score
CLI scanner
```

Goal:

A fully working technical scanner.

---

## Phase 2 — Backtesting

Implement:

```text
historical simulation
transaction costs
stop loss
take profit
position sizing
performance metrics
benchmark comparison
```

Goal:

Validate whether the technical core has statistical value.

---

## Phase 3 — News & KAP

Implement:

```text
KAP ingestion
financial news ingestion
LLM classification
event extraction
news decay
news score
price/news divergence
```

Goal:

Determine whether event information improves strategy performance.

---

## Phase 4 — Institutional Flow

Implement:

```text
BOFA / major broker flow
1D/3D/5D/10D aggregation
turnover normalization
flow persistence
flow acceleration
price-flow divergence
custody confirmation
```

Goal:

Measure whether institutional flow improves forward returns.

---

## Phase 5 — Composite Model

Combine:

```text
technical
news
institutional
market regime
risk
```

Run:

```text
walk-forward optimization
out-of-sample validation
factor ablation
```

---

## Phase 6 — Dashboard

Build:

```text
ranking
stock detail page
score breakdown
technical charts
news feed
broker flow
backtest metrics
paper portfolio
```

---

## Phase 7 — Paper Trading

Implement:

```text
virtual portfolio
order simulation
daily performance
signal tracking
trade journal
```

---

## Phase 8 — Optional Live Trading

Only after successful validation.

---

# 76. Research Methodology

Every new feature should answer:

```text
Does this improve out-of-sample returns?
```

Perform ablation tests.

Example:

```text
Model A:
Technical only

Model B:
Technical + News

Model C:
Technical + Institutional Flow

Model D:
Technical + News + Flow

Model E:
All + Market Regime
```

Compare:

```text
CAGR
Sharpe
Sortino
Max Drawdown
Profit Factor
Win Rate
Expectancy
Trade Count
```

If a feature does not improve the strategy, remove it.

---

# 77. Important Statistical Principle

Do not optimize for:

```text
maximum win rate
```

A strategy can be profitable with:

```text
Win Rate = 45%
```

if:

```text
Average Winner >> Average Loser
```

Focus on:

```text
expectancy
profit factor
drawdown
risk-adjusted return
```

---

# 78. Example Strategy V1

Potential experimental V1:

```text
Market:
XU100 > EMA200

Stock:
Close > EMA50
EMA50 > EMA200

Momentum:
RSI between 45 and 70

MACD:
MACD > Signal

Volume:
Volume > VolumeMA20 * 1.20

Relative Strength:
20D stock performance > XU100 20D performance

Risk:
R:R >= 2
```

This should be considered a **starting hypothesis**, not a final strategy.

---

# 79. Example Scoring Pseudocode

```python
def calculate_score(features):
    score = 0

    score += trend_score(features)
    score += momentum_score(features)
    score += volume_score(features)
    score += relative_strength_score(features)
    score += news_score(features)
    score += institutional_flow_score(features)
    score += market_regime_score(features)
    score += volatility_score(features)

    return min(max(score, 0), 100)
```

---

# 80. Example Signal Pseudocode

```python
def generate_signal(features):
    score = calculate_score(features)

    rr = calculate_risk_reward(features)

    if not features.liquidity_ok:
        return "NO_TRADE"

    if features.market_regime == "BEAR":
        required_score = 88
    elif features.market_regime == "NEUTRAL":
        required_score = 80
    else:
        required_score = 75

    if score >= required_score and rr >= 2.0:
        return "BUY_CANDIDATE"

    if score >= 50:
        return "WATCH"

    return "NO_TRADE"
```

---

# 81. AI Coding Agent Instructions

When implementing this project:

1. Keep modules small and independent.
2. Use type hints everywhere.
3. Use Pydantic for structured domain models.
4. Use async I/O for external APIs.
5. Never hardcode API credentials.
6. Never hardcode a single data provider into strategy code.
7. Write tests before adding complex scoring logic.
8. Keep all scoring weights configurable.
9. Keep all indicator parameters configurable.
10. Do not mix market-data ingestion with signal generation.
11. Do not let LLM output directly execute trades.
12. Do not introduce machine learning before the rule-based system is validated.
13. Store raw source data whenever legally and technically possible.
14. Add structured logging to every external integration.
15. Protect against missing/stale market data.
16. Explicitly document assumptions.
17. Prevent look-ahead bias.
18. Include transaction costs in all performance reports.
19. Prefer reproducible research over ad-hoc notebooks.
20. Every signal must be explainable.

---

# 82. First Implementation Task

Build **Phase 1 only** before continuing.

Deliverables:

```text
1. Python package
2. Config loader
3. Market data provider interface
4. One working market data adapter
5. BIST symbol universe loader
6. EMA calculation
7. RSI calculation
8. MACD calculation
9. Bollinger Bands
10. ATR
11. ADX
12. Volume ratios
13. Relative strength vs XU100
14. Market regime classifier
15. Technical scoring engine
16. CLI scanner
17. Unit tests
18. README
```

CLI target:

```bash
python -m bist_quant scan
```

Example output:

```text
SYMBOL   SCORE   SIGNAL
THYAO      82    BUY_CANDIDATE
ASELS      79    BUY_CANDIDATE
TUPRS      74    WEAK_SETUP
BIMAS      67    WEAK_SETUP
KCHOL      61    WATCH
```

---

# 83. Definition of Done for Phase 1

Phase 1 is complete when:

- At least 20 BIST symbols can be downloaded.
- XU100 data can be downloaded.
- Technical indicators are calculated correctly.
- No NaN-related crashes occur after warm-up periods.
- Each stock receives a technical score.
- Market regime modifies signal thresholds.
- CLI prints a ranked table.
- Unit tests pass.
- Configuration is externalized.
- No future data is used.
- Code is documented.
- Project runs from a clean environment using documented setup steps.

---

# 84. Future Enhancements

Possible later additions:

```text
fundamental factor scoring
earnings surprise
valuation ratios
foreign investor ownership
sector rotation
breadth indicators
market internals
correlation filters
portfolio optimization
economic calendar
central bank events
FX sensitivity
commodity sensitivity
machine learning meta-model
anomaly detection
Telegram notifications
Discord notifications
mobile dashboard
broker execution
```

---

# 85. Machine Learning — Later Only

After the rule-based system has enough historical data, ML can be used as a **meta-model**.

Instead of predicting price directly:

```text
"What will THYAO price be tomorrow?"
```

predict:

```text
"Given this technical/news/flow setup,
what is the probability that TP1 is reached
before the stop within the next 10 sessions?"
```

Example target:

```text
1 = TP reached before stop
0 = stop reached first
```

Potential models:

```text
Logistic Regression
Random Forest
XGBoost
LightGBM
CatBoost
```

Start with interpretable models.

---

# 86. Final Product Vision

The final application should behave like a BIST quantitative research and signal platform.

Example user experience:

```text
TOP BIST OPPORTUNITIES

1. THYAO
Score: 86
Trend: Strong
Momentum: Positive
News: Positive
Institutional Flow: Accumulating
Market: Bullish
Risk/Reward: 2.6

2. ASELS
Score: 82
Trend: Strong
Momentum: Positive
News: Strong Positive
Institutional Flow: Neutral
Risk/Reward: 2.3
```

A detail page should show:

```text
Price chart
EMA overlays
RSI
MACD
Volume
Bollinger Bands
ATR
Relative Strength
News timeline
KAP disclosures
Broker flow
Custody changes
Score history
Signal history
Backtest behavior
```

---

# 87. Core Philosophy

The system should follow this hierarchy:

```text
DATA
  ↓
FEATURES
  ↓
FACTORS
  ↓
SCORE
  ↓
RISK FILTER
  ↓
SIGNAL
  ↓
BACKTEST
  ↓
PAPER TRADE
  ↓
OPTIONAL LIVE EXECUTION
```

Never:

```text
INDICATOR
  ↓
BUY
```

The goal is not to create a bot that frequently trades.

The goal is to create a bot that can identify a **small number of high-quality, statistically defensible setups**.

---

# 88. Important Project Rule

No factor should be trusted because it "sounds logical."

Every factor must eventually be tested using historical data.

Questions should be answered statistically:

```text
Does BOFA flow add value?

Does positive KAP sentiment improve returns?

Does MACD matter after controlling for trend?

Does volume confirmation improve breakout performance?

Does XU100 regime filtering reduce drawdown?
```

If the answer is no, remove the factor.

---

# 89. Initial Milestone Order

Recommended implementation order:

```text
1. Repository setup
2. Configuration system
3. Market data adapter
4. Technical indicators
5. XU100 regime
6. Scoring engine
7. CLI scanner
8. Database
9. Backtesting
10. Risk management
11. News/KAP ingestion
12. LLM news classifier
13. Institutional flow
14. Custody data
15. Composite backtest
16. Dashboard
17. Paper trading
18. Optional broker integration
```

---

# 90. Project Success Criteria

The project should not be considered successful simply because historical returns are high.

Success requires:

```text
reasonable trade count
stable results across years
acceptable maximum drawdown
positive expectancy
robustness to parameter changes
out-of-sample profitability
transaction-cost resilience
paper-trading consistency
explainable signals
```

The ultimate goal is:

> A modular, explainable, statistically validated Borsa İstanbul swing-trading research and signal system combining technical analysis, market regime, news/KAP intelligence, institutional flow, and disciplined risk management.

