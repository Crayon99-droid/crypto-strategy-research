# Report: crypto volatility risk premium screen v1

Protocol PROTOCOL_VRP_v1.md, SHA-256 c927f43a18c572a88febc3f4c6158a04ab7c43c238e5501da31e988cf245fa1d.
Data: Deribit DVOL hourly BTC/ETH 2021-03-24 to 2026-11-04 (zero gaps), Bybit perps.
Engine validated on synthetic markets (zero premium gives ~0 gross; a 10-point
premium gives the theoretical ~27%/yr gross). This is a model backtest with
Black-Scholes, flat smile and assumed spreads, not real option quotes.

## Part A: implied minus realised volatility (30-day), development

| | Mean | Median | Days positive | Worst 1% |
|---|---|---|---|---|
| BTC | +10.2 pts | +11.4 | 77% | -41 pts |
| ETH | +6.7 pts | +7.8 | 69% | -77 pts |

By year (BTC / ETH mean): 2021 16.9 / 10.4; 2022 12.7 / 9.8; 2023 6.6 / 5.2;
2024 5.7 / 1.7. The premium shrank every year.

## Part B: daily ladder of delta-hedged short 30-day straddles

Development, BTC+ETH combined: PASS on all four gates.

| Setting | Ann. return | Sharpe | Max DD | Worst day | Worst 30 days | Boot p |
|---|---|---|---|---|---|---|
| Base | 15.0% | 1.18 | -15.2% | -5.8% | -12.3% | 0.012 |
| Stress | 7.0% | 0.55 | -16.1% | -5.9% | -12.9% | 0.14 |

By year (base): 2021 +22.8%, 2022 +27.6%, 2023 +4.4%, 2024 +1.9%.
Stress: 2023 and 2024 negative.

Holdout (2025-01-01 to 2026-11-04), opened once:

| Setting | Ann. return | Sharpe | Max DD |
|---|---|---|---|
| Base | +0.7% | 0.07 | -10.7% |
| Stress | -7.1% | -0.64 | -16.7% |

Holdout gates (net > 0; max DD within 1.5x development) technically pass. The
economic result is nil: ETH lost money, BTC made 4.4%, and the stress setting
lost 7%/yr.

## Conclusion
The premium was large in 2021-22 and has been near zero after realistic costs since
2023. Selling volatility now means taking crash risk (worst day -7%) for no
measurable compensation. Not recommended for a bot. Re-test only with real
historical option quotes, and only if DVOL-minus-realised widens again.
