# Pre-registered protocol: crypto time-series trend following (v1)

Status: FROZEN. Approved by the user 2026-11-05 01:11 (UTC+5), before any
strategy result was computed. SHA-256 of this file is recorded in
PROTOCOL_TSMOM_v1.sha256. Any later change is a new version (v2).

## 1. Hypothesis

Liquid crypto perpetuals show time-series momentum at multi-day to multi-week
horizons: after a price breaks out of its recent range, its subsequent return is
on average in the breakout direction, by more than round-trip costs plus funding.

Economic rationale: slow information diffusion, herding and leverage cycles in
crypto; trend following is documented across asset classes and in crypto
(e.g. Zarattini, Pagani & Barbon 2025, SSRN 5209907). Expected payoff profile:
win rate roughly 30-45%, winners much larger than losers.

## 2. Data

- Source: Bybit V5 public API, USDT linear perpetuals, 1-hour OHLCV and
  historical funding, downloaded by the user on 2026-11-05.
- Audit: 30 symbols, zero gaps, zero OHLC violations. Largest hourly move
  (UNI +56%, 2024-02-23) verified as a genuine event. Nothing interpolated.
- Daily bars are built from hourly bars on UTC days (00:00-23:59).
- Funding is applied at its actual timestamps (intervals vary: STX used
  1-hour funding in 2022-23).

### Universe and its known bias
The 30 highest-turnover USDT perps on 2026-11-05 that were listed before
2022-01-01. Chosen with today's information, and delisted coins are absent:
results on the 28 altcoins are biased upward (survivorship).
Primary evidence therefore = BTC and ETH. The 30-coin portfolio is reported
as supporting evidence only.

## 3. Time split

| Segment | Dates (UTC) | Use |
|---|---|---|
| Warm-up | first 100 days of each symbol | indicators only, no trades |
| Development | start to 2024-12-31 | all results in the first report |
| Holdout | 2025-01-01 to 2026-11-04 | opened once, after the development report, with no rule changes |

Caveat: 2025-26 ETH/XRP price action was seen in the user's earlier,
unrelated DC research. It has never been used for this strategy.

## 4. Strategy rules (fixed; no parameter search)

Signal: an equal-weight ensemble of three Donchian breakout sub-systems, so
that no single lookback is selected.

| Sub-system | Entry lookback N (days) | Exit lookback N/2 (days) |
|---|---|---|
| A | 20 | 10 |
| B | 50 | 25 |
| C | 100 | 50 |

For each symbol and sub-system, evaluated at each UTC daily close:
- Long entry: daily close > highest high of the previous N days.
- Short entry: daily close < lowest low of the previous N days.
- Entry fill: open of the next hourly bar, plus slippage.
- Exit (long): stop at the lowest low of the previous N/2 days, updated daily,
  only ever raised. Checked on every hourly bar; if an hourly low touches it,
  fill at min(stop, bar open) minus slippage. Mirror rule for shorts.
- After an exit, a new entry needs a fresh breakout signal.
- No profit targets. No other exits.

Position size (volatility-scaled, equal risk per symbol):
  weight_i = s_i x (0.50 / N_symbols) / sigma_i
where s_i in {-1, -2/3, ..., +1} is the sum of the three sub-system positions
divided by 3, and sigma_i is the annualised volatility of the last 30 daily
returns, fixed at entry. Gross exposure capped at 2.0x equity (pro-rata scale-down).
For a single-asset test (BTC alone, ETH alone), N_symbols = 1.
The 0.50 scale sets leverage only; Sharpe ratios and hit rates do not depend on it.
Positions are not resized while open.

## 5. Variants (the complete comparison family)

1. Primary: long and short (rules above).
2. Secondary: long only (short entries ignored).

Nothing else is tested in v1. Each variant uses a one-sided alpha of 0.025
(Bonferroni over 2).

## 6. Costs

- Taker fee 0.055% per fill (Bybit base tier), all entries and exits.
- Slippage 0.05% per fill, all symbols.
- Funding: actual historical rates on position notional at each funding time.
- Stress test: fees and slippage doubled (0.21% per fill).
- No maker fills are assumed anywhere.

## 7. Benchmarks and placebo

- Buy-and-hold BTC; equal-risk buy-and-hold of the same universe.
- Random-timing placebo: 1,000 runs that keep each sub-system's number of
  trades, holding durations and long/short mix per symbol but draw entry dates
  at random. The strategy must beat the placebo's 95th percentile.

## 8. Statistics

- Unit: daily portfolio net returns. Trade-level stats are descriptive.
- Inference: moving-block bootstrap, 20-trading-day blocks, 10,000 replications;
  one-sided lower bound on mean daily net return at alpha 0.025.
- Also reported: annualised return and volatility, Sharpe, max drawdown and
  duration, trade count, win rate, payoff ratio, profit factor, results by
  calendar year, by symbol, and long vs short.

## 9. Decision rules (set before any results)

Development PASS requires all of:
1. Block-bootstrap lower bound on mean daily net return > 0 (alpha 0.025), for
   BTC+ETH, and for the 30-coin portfolio.
2. Beats the random-timing placebo's 95th percentile.
3. Net mean stays > 0 under doubled costs.
4. Positive net return in at least 3 of 4 calendar years 2021-2024.
5. Not explained by drift: the long-only variant must beat buy-and-hold on a
   risk-adjusted basis (Sharpe), or the long-short variant must earn positive
   returns from its shorts.

Holdout PASS (opened only if development passes) requires:
net return > 0, Sharpe >= 0.5, and max drawdown no worse than 1.5x the
development maximum. 22 months cannot prove an edge statistically; this gate
only checks that it did not break.

If development fails: report the null result. Do not tune the rules on the same
data. A v2 must state a new reason, not a new parameter set.

If both pass: 1-minute execution modelling, then testnet, then small live,
with a pre-set kill rule (live drawdown > 1.5x the holdout maximum).
