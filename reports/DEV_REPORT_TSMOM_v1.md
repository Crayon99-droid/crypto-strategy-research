# Development report: TSMOM v1 (Donchian ensemble, Bybit perps)

Protocol: PROTOCOL_TSMOM_v1.md, SHA-256 5b41b5bc9b8218661d5ad63aa60aa6dcb276c73228612026947b9110a10cc14a,
frozen before any result. Data: user download 2026-11-05, fingerprints in DATA_MANIFEST.sha256.
Development window: 2020-07-05 (after warm-up) to 2024-12-31. No row from 2025-01-01 onward was loaded.

## Verdict: FAIL. Holdout not opened.

Gates 1 (statistical significance) and 5 (not explained by drift) failed for
both variants and both decisive universes. Per the protocol, the holdout stays
closed and the rules are not tuned on this data.

## Engine verification
- 25 random trades re-derived with independent pandas code: signal, entry and
  exit matched 25/25.
- Daily ledger total equals the sum of per-trade P&L (to 1e-15).
- Manual P&L of a multi-day trade matches the engine exactly.
- The 2x gross-exposure cap never bound.

## Decisive results (net of fees, slippage and funding; reference capital 1.0)

| Universe / variant | Ann. return | Sharpe | Max DD | Trades | Win rate | Payoff | Boot p(mean<=0) | Placebo pct | B&H Sharpe |
|---|---|---|---|---|---|---|---|---|---|
| BTC+ETH long/short | 27.3% | 0.51 | -73.9% | 152 | 42% | 2.78 | 0.133 | 95.9 | 1.01 |
| BTC+ETH long only | 32.1% | 0.62 | -72.3% | 84 | 50% | 3.84 | 0.085 | 95.8 | 1.01 |
| 30-coin long/short | 15.5% | 0.48 | -45.1% | 1,872 | 39% | 2.56 | 0.153 | 100.0 | 0.54 |
| 30-coin long only | 16.2% | 0.53 | -40.5% | 909 | 36% | 4.05 | 0.140 | 99.9 | 0.54 |

Return and drawdown levels depend on the 0.50 leverage scale; Sharpe does not.

| Gate | BTC+ETH LS | BTC+ETH LO | 30-coin LS | 30-coin LO |
|---|---|---|---|---|
| 1 Bootstrap lower bound > 0 | fail | fail | fail | fail |
| 2 Beats placebo 95th pct | pass | pass | pass | pass |
| 3 Positive at 2x costs | pass | pass | pass | pass |
| 4 >= 3 of 4 years positive | pass | pass | pass | fail |
| 5 Not drift (LO > B&H Sharpe, or shorts profitable) | fail | fail | fail | fail |

## What the failure is made of
1. Costs were not the problem. Doubling fees and slippage moved Sharpe by ~0.02.
   Fees were about 1-2% of gross price P&L (few, long trades).
2. Funding was the largest cost. For BTC+ETH it consumed 39% of gross price
   gains (0.82 of 2.08). Bybit long funding ran 70-125% annualised from Nov 2020
   to Apr 2021, exactly when the strategy was fully long.
3. Shorts lost money in every universe. The edge, such as it is, is long-side.
4. The timing signal is real but small. It beat random-timing trades with the
   same durations and sides at the 96th-100th percentile, yet it did not beat
   simply holding the coins, which the 2020-24 bull market favoured.
5. Concentration: 2020 H2 produced 57% of BTC+ETH long/short profit.

## Post-hoc diagnostics (seen after the verdict; not evidence)
- 2021-2024 only: BTC+ETH Sharpe 0.24 (LS) / 0.35 (LO) vs buy-and-hold 0.76.
  30-coin: 0.47 / 0.53 vs 0.54.
- With funding removed (as if traded spot): BTC+ETH Sharpe 0.85 (LS) / 0.99 (LO);
  30-coin 0.62 / 0.69. Still not above buy-and-hold for BTC+ETH.

## Limits
- One venue; 4.5 years with one dominant bull cycle; universe has survivorship bias.
- Hourly bars: intrabar order inside the stop bar is approximated.
- Additive accounting on a constant reference capital.
