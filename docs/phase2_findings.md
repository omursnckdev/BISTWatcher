# Phase 2 findings (data to 2026-09-25)

These are the results of the default configuration on Yahoo data. Costs are 0.205% per
side (commission 0.10% + fee 0.005% + slippage 0.10%), and idle cash earns 0%.
**Caveat:** both universes are *today's* index members, so survivorship bias inflates
every number here, the equal-weight benchmark most of all.

Reproduce with `backtest`, `sweep`, `walkforward` and `research`, with and without
`--universe BIST100`.

## 1. Does the score predict returns? (`research`)

| Universe | Rank IC 5D (t) | Rank IC 20D (t) | 20D excess return, score 85+ | 20D excess return, score < 50 |
|---|---|---|---|---|
| BIST30 | +0.005 (0.5) | +0.022 (1.0) | +0.04% | −0.08% |
| BIST100 | +0.021 (3.0) | +0.040 (2.7) | +0.82% | −0.45% |

- **BIST30:** no statistically meaningful edge. The 30 largest, most-followed names are hard to rank with public technical data.
- **BIST100:** a small but significant and monotonic edge. Excess returns rise steadily across the score buckets.
- **Regime:** forward returns were *not* lower after BEAR days. BEAR had the highest 5–20D returns, so the regime threshold adjustment is not supported by this data.

## 2. Resistance cap and thresholds (`sweep`, BIST30, 2016–2026)

| Threshold offset | Resistance cap | Trades | PF | Expectancy (R) | CAGR % | Max DD % | Sharpe |
|---|---|---|---|---|---|---|---|
| −5 | on | 1237 | 1.30 | 0.14 | 14.7 | −26.9 | 1.03 |
| −5 | off | 1636 | 1.18 | 0.11 | 12.8 | −39.9 | 0.81 |
| 0 | on | 1089 | 1.27 | 0.14 | 12.3 | −25.9 | 0.94 |
| 0 | off | 1431 | 1.18 | 0.11 | 12.2 | −41.4 | 0.82 |
| +10 | on | 650 | 1.37 | 0.20 | 9.8 | −16.6 | 0.95 |
| +10 | off | 880 | 1.26 | 0.16 | 10.4 | −28.3 | 0.86 |

- **Resistance cap:** it helps at every threshold, with a higher profit factor and expectancy and a much smaller drawdown.
- **Thresholds:** higher thresholds give better trades but fewer of them. The change is smooth, with no knife-edge optimum, so the default region is robust.

## 3. Out-of-sample (`walkforward`, 3-year train / 1-year test, 2019–2026)

| | CAGR % | Max DD % | Sharpe | Trades | PF |
|---|---|---|---|---|---|
| Strategy, BIST30 | 17.4 | −25.4 | 1.11 | 1036 | 1.45 |
| Strategy, BIST100 | 28.4 | −26.4 | 1.59 | 1323 | 1.53 |
| XU100 buy & hold | 41.4 | −31.8 | 1.43 | – | – |

- **BIST100:** better *risk-adjusted* than the index (higher Sharpe, smaller drawdown).
- **Absolute return:** lower than the index. The strategy is invested only about 40% of the time, and in a high-inflation, strongly rising nominal TRY market, uninvested cash at 0% is a large drag.
- **Parameter stability:** the parameters chosen in each fold change from year to year, and the 2025–2026 test years lost money.

## Implications for the next phases

1. **Idle-cash yield.** Model it (`backtest.cash_interest_annual_pct`) before comparing with buy & hold in TRY.
2. **Wider universe.** Prefer BIST100 (or wider): the technical edge only appears across a larger cross-section.
3. **Regime thresholds.** Revisit or drop the BEAR / HIGH_VOLATILITY threshold increases; the data does not support them.
4. **Historical index membership.** Obtain it to remove survivorship bias before trusting any absolute number.
5. **News/KAP and broker flow (Phases 3–4).** Each must add out-of-sample value on top of this baseline, or be removed.
