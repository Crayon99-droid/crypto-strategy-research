# Report: funding carry v1 (long spot + short perp, Bybit)

Protocol PROTOCOL_CARRY_v1.md, SHA-256 eb94edc4cc8ce2372b9e76750c823823463b558577b40d28fe9e459eeeac2d29.

## Engine verification
- Synthetic market with known funding and a moving basis: engine = independent
  calculation to 6 decimals.
- Real data, BTC always-on 2021-07 to 2024-12: engine 0.509165 = independent
  pandas recomputation 0.509165.
- Two bugs were found and fixed during verification, before any gate was read:
  00:00 funding payments were skipped; the 7-day warm-up was not enforced on entry.
  A third (date ordering when coins start on different days) truncated the 27-coin
  window; fixed before reading its result.

## Development (2021-07-13 to 2024-12-31): PASS, both variants

| Universe / variant | Ann. return | Sharpe | Max DD | Worst day | 2x cost ann. | Trades | Time in market |
|---|---|---|---|---|---|---|---|
| BTC+ETH conditional | 7.6% | 5.68 | -0.80% | -0.80% | 7.0% | 8 | 40% |
| BTC+ETH always-on | 14.2% | 7.01 | -1.29% | -1.29% | 13.9% | 2 | 100% |
| 27-coin conditional | 9.5% | 6.44 | -0.63% | -0.56% | 8.8% | 112 | 90% |
| 27-coin always-on | 5.0% | 5.30 | -1.58% | -0.29% | 4.9% | 27 | 100% |

All five gates passed for every row (bootstrap, 2x cost, years, drawdown, 5% hurdle).

## Holdout (2025-01-08 to 2026-11-04), opened once, rules unchanged

| Universe / variant | Ann. return | Max DD | Holdout gates |
|---|---|---|---|
| BTC+ETH conditional | 0.0% (never entered) | 0% | FAIL (net not > 0) |
| BTC+ETH always-on | 3.3% | -0.25% | PASS |
| 27-coin conditional | 0.03% | -0.07% | pass, economically nil |
| 27-coin always-on | 1.3% | -0.47% | PASS |

BTC+ETH always-on by quarter: 2025 Q1-Q4 +0.77, +0.82, +2.24, +0.81%;
2026 Q1-Q4 (to Nov 4) +0.36, +0.15, +0.81, +0.11%.

Disclosure: BTC average funding for 2025 (~5%/yr) and 2026 (~3%/yr) had been seen in
an earlier diagnostic before the holdout was opened. Rules were frozen before that.

## Conclusion
The premium is real and low-risk in this sample, but it has shrunk from ~14%/yr to
~3%/yr, and to ~1.7%/yr annualised in 2026, below the 5% economic hurdle. The
conditional rule correctly stayed out for 22 months (funding never reached 15%/yr).

## Risks not in the backtest
- Exchange counterparty risk: both legs and all collateral sit on one exchange.
- Margin: the short perp leg lost up to 222% of notional on BTC during holds,
  offset by the spot leg only if spot is accepted as collateral (unified account,
  collateral haircut). A buffer is required.
- Spot fee 0.10% assumed (Bybit non-VIP); funding paid on mark price approximated
  by the perp hourly open.
