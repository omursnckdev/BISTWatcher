# Phase 3 findings: KAP news (data to 2026-09-25)

Universe: today's BIST100 members, so survivorship bias applies to every number below.
Period: 2016–2026. Data: about 126,000 KAP disclosures for the 101 companies. About 64,000
events remain after dropping administrative filings. Classifier: the deterministic
Turkish rules (`news.classifier: rules`).

Reproduce: `kap-sync --universe BIST100 --start 2015-12-01`, then `research`, and
`backtest` / `sweep` with and without `--no-news`.

## 1. Event study: excess return vs the universe average, from the next open

The baseline must be the universe average, not XU100: today's BIST100 members beat XU100
by roughly 1% per 20 days (survivorship). Against XU100, every event type looks positive.
The t-stats below are optimistic because windows overlap and events cluster.

| Event (rule tone) | n | 20D excess | 2016–20 | 2021–26 | Stable? |
|---|---|---|---|---|---|
| Rights issue (bedelli, negative) | 247 | −3.8% (t −3.1) | −0.8% | −5.6% | yes |
| Exchange / SPK measure (negative) | 1275 | −1.4% (t −3.7) | −0.4% | −1.7% | yes |
| Share conversion (negative) | 3358 | −0.8% (t −3.2) | −0.5% | −0.9% | yes |
| Lawsuit (negative) | 833 | −0.9% (t −2.4) | −0.5% | −1.4% | yes |
| New contract (positive) | 3277 | +1.5% (t 4.4) | +1.9% | +1.4% | yes |
| Share buyback (positive) | 2865 | +1.1% (t 5.0) | +0.5% | +1.3% | yes |
| Bonus issue (bedelsiz, positive) | 384 | −1.6% | +3.8% | −2.5% | **no** |
| Insider / holder selling | 186 | +7.5% | +14.5% | −2.9% | **no** |
| M&A (positive) | 1193 | −0.3% | −0.3% | −0.4% | wrong sign, small |

News vs price reaction (spec §24), entry after the reaction:

| Pattern | 20D excess | Spec hypothesis |
|---|---|---|
| Good news, no reaction | +0.8% | "weakness": **not supported** (as good as confirmation) |
| Good news, negative reaction | −0.2% (5D −0.7%, t −4.8) | "weakness": supported short-term |
| Bad news, positive reaction | −0.6% | "hidden strength": **not supported** |
| Neutral news, negative reaction | −0.7% (t −4.6) | a negative reaction persists |

## 2. Portfolio backtest: technical only vs with news

BIST100, 2016–2026, full costs; same data in one run.

| Variant | Offset | Trades | PF | Expectancy | CAGR | Max DD | Sharpe |
|---|---|---|---|---|---|---|---|
| No news | −10 | 1748 | 1.27 | 0.13R | 18.2% | −30.0% | 1.11 |
| No news | 0 | 1427 | 1.35 | 0.17R | 19.6% | −23.9% | 1.30 |
| No news | +5 | 1206 | 1.38 | 0.20R | 19.4% | −21.2% | 1.34 |
| **News, a-priori rules** | −10 | 1650 | 1.33 | 0.16R | 21.4% | −26.6% | 1.31 |
| **News, a-priori rules** | 0 | 1177 | 1.41 | 0.23R | 20.4% | −21.5% | 1.42 |
| **News, a-priori rules** | +5 | 912 | 1.52 | 0.27R | 20.5% | −19.8% | 1.57 |
| News, event-study calibrated | −10 | 1637 | 1.27 | 0.14R | 18.6% | −27.7% | 1.16 |
| News, event-study calibrated | 0 | 1167 | 1.45 | 0.23R | 21.0% | −21.5% | 1.44 |
| News, event-study calibrated | +5 | 894 | 1.44 | 0.23R | 16.7% | −20.9% | 1.34 |

- **Rules written before seeing any results improve every threshold level.** They raise profit factor, expectancy and Sharpe, and reduce drawdown. This is the cleanest evidence that KAP news adds information.
- **Calibration did not help.** "Calibrating" the rules on the event study (neutralising unstable event types and reaction effects) did **not** improve the portfolio. Event-level statistics did not carry over to trade selection, and choosing them would have been in-sample fitting anyway. The a-priori rules are therefore the default, and the calibrated values stay available through `news.sentiment_overrides` and `news.reaction.multipliers`.

## 3. Walk-forward (out-of-sample 2019–2026)

Rolling 3-year train / 1-year test windows; parameters are chosen in-sample by Sharpe,
and the test years are chained together.

| Variant | Grid | CAGR | Max DD | Sharpe | Trades | PF | Expectancy |
|---|---|---|---|---|---|---|---|
| No news | threshold offset | 21.5% | −28.3% | 1.30 | 1198 | 1.43 | 0.18R |
| **News (a-priori)** | threshold offset | 23.4% | −21.5% | 1.54 | 924 | 1.56 | 0.23R |
| No news | offset × resistance cap | 28.4% | −26.4% | 1.59 | 1323 | 1.53 | 0.21R |
| **News (a-priori)** | offset × resistance cap | 27.4% | **−17.5%** | **1.74** | 981 | **1.63** | **0.26R** |
| XU100 buy & hold | – | 41.4% | −31.8% | 1.43 | – | – | – |

- **Out of sample:** KAP news improves every risk-adjusted metric: Sharpe +0.15–0.24, profit factor +0.10–0.13, expectancy +0.05R. It also cuts the maximum drawdown by 7–9 points.
- **Versus XU100:** with news, the strategy beats XU100 on Sharpe (1.74 vs 1.43) and drawdown (−17.5% vs −31.8%). It still earns less in absolute TRY terms because it is invested only about 35% of the time and idle cash earns 0% in the backtest.

## Decisions

1. **Keep the news factor:** it passes the spec's test ("does it improve out-of-sample results?").
2. **Default to the a-priori rules.** The event-study calibration stays documented but off.
3. **Treat the event study as research, not as a reason to change rules.** Any change must win in the portfolio backtest and walk-forward, not only in event averages.
4. **Test the LLM classifier (`claude` / `hybrid`) before relying on it.** It has not been backtested (no API key here). Run the same comparison before trusting it; the classifications are cached, so a backtest pays for each disclosure only once.
