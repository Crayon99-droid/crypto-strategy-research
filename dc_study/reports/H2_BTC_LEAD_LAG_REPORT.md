# H2 BTC-to-ETH/XRP Lead–Lag Evaluation

## Decision

**H2 failed. No BTC-filtered threshold policy passed the preregistered evidence gates.**

The tested hypothesis was narrow and frozen before BTC data or outcomes were supplied: an ETH/XRP directional-change trade was retained only when BTC's direction-aligned five-minute return, normalized by trailing 240-minute realized volatility, was at or above the causal 75th percentile of the preceding 180 calendar days for the same target symbol and detector multiplier.

Per the stopping rule, no alternative BTC lag, percentile, volatility window, or leading asset may now be tried under H2.

## Data authenticity and integrity

- Source: direct public Bybit V5 `BTCUSDT` linear-perpetual one-minute kline requests recorded in the supplied provenance files.
- Period: 2021-01-01 00:00 UTC through 2024-12-31 23:59 UTC.
- BTC candles audited: **2,103,840**.
- HTTPS request-ledger entries audited: **2,106**.
- Every yearly archive matched its manifest SHA-256 hash and ZIP CRC.
- Schema, numeric values, OHLC invariants, timestamp text, strict one-minute chronology, annual boundaries, duplicate timestamps, and complete year coverage all passed.
- The instrument file and request-ledger file matched their manifest hashes.
- No repair, interpolation, forward-fill, or synthetic candle generation was performed.
- The request ledger retains response hashes and request boundaries, not the raw API response bodies. This limitation is recorded explicitly; the delivered candle archives were independently checked in full.

The target-side checkpoint also passed: the 388,338 frozen ETH/XRP candidate events were read from the exact Phase 5 archive, contained zero entries in 2025–2026, and all nine fixed-threshold trade streams reproduced their frozen candidate IDs and returns exactly.

## Frozen method

At each ETH/XRP signal-bar close \(t\):

\[
B_t = \operatorname{side}_t
\frac{\log(P^{BTC}_t/P^{BTC}_{t-5m})}
{\sqrt{\sum_{i=t-239m}^{t}r_i^2}}
\]

The event passed when \(B_t\) was at or above its causal prior-event 75th percentile over the previous 180 calendar days. The current event was excluded, at least 50 prior events were required, and filtering occurred before non-overlap selection.

All nine independent detector multipliers were evaluated as one family:

`0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00, 2.50, 3.00`

Trade mechanics remained frozen: next-bar-open market-taker entry, detector-extreme stop, 1.5:1 reward/risk, 240-minute maximum hold, conservative stop-first same-minute handling, one position per target symbol/policy, adverse tick rounding, 5.5 bp taker fee per fill, 1 bp slippage per side, and historical funding.

Inference used 20,000 four-week circular moving-block bootstrap replicates. Simultaneous one-sided 98% max-t bounds were calculated separately for absolute filtered performance and the improvement over each matched fixed-threshold baseline.

## Results

| k | Filtered trades | Baseline mean (bp) | Filtered mean (bp) | PF | TP | SL | Timeout | Absolute 98% lower (bp) | Improvement (bp) | Improvement 98% lower (bp) | Pass |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 0.25 | 20,401 | -13.99 | -14.20 | 0.547 | 37.43% | 60.58% | 1.99% | -15.31 | -0.20 | -1.32 | No |
| 0.50 | 7,355 | -13.09 | -14.54 | 0.673 | 33.32% | 53.58% | 13.09% | -17.57 | -1.45 | -4.08 | No |
| 0.75 | 4,014 | -11.87 | -14.69 | 0.730 | 27.20% | 45.57% | 27.23% | -20.66 | -2.82 | -7.37 | No |
| 1.00 | 2,541 | -12.39 | -10.68 | 0.819 | 23.06% | 35.54% | 41.40% | -18.12 | +1.71 | -4.61 | No |
| **1.25** | **1,797** | **-8.97** | **-7.74** | **0.875** | **19.70%** | **26.77%** | **53.53%** | **-18.58** | **+1.23** | **-8.09** | **No** |
| 1.50 | 1,345 | -11.33 | -15.75 | 0.778 | 15.99% | 23.94% | 60.07% | -30.71 | -4.42 | -17.83 | No |
| 2.00 | 817 | -15.62 | -21.49 | 0.713 | 11.63% | 16.28% | 72.09% | -37.99 | -5.87 | -19.49 | No |
| 2.50 | 541 | -15.28 | -29.52 | 0.637 | 8.87% | 9.80% | 81.33% | -56.75 | -14.24 | -37.45 | No |
| 3.00 | 403 | -25.33 | -45.60 | 0.522 | 6.20% | 5.96% | 87.84% | -84.05 | -20.26 | -51.92 | No |

The least-negative filtered policy was \(k=1.25\), not a passing policy. Its BTC filter raised pre-cost signal-path mean from **+4.86 bp** to **+6.02 bp**, but mean execution cost plus funding was approximately **13.93 bp**, leaving **−7.74 bp/trade**. The estimated improvement over the matched baseline was only **+1.23 bp**, with a simultaneous 98% lower bound of **−8.09 bp**.

For \(k=1.25\), 2023 averaged −12.06 bp/trade and 2024 averaged −3.21 bp/trade. Only 2024 H2 was positive (+7.82 bp); the other three half-years were negative. The filter therefore failed both economic and temporal-stability gates.

One target/side subgroup—XRP longs—was positive (+4.82 bp), while ETH longs, ETH shorts, and XRP shorts were negative. This is a rejected-policy, post-hoc diagnostic and is not evidence for a new tradable rule.

## Interpretation

The BTC impulse filter did not rescue the price-only directional-change strategy. It modestly improved two middle-threshold means, but the improvement was too small and uncertain to cover execution costs. At larger thresholds it selected worse trades and left 60%–88% unresolved at the 240-minute timeout.

This result does **not** claim that all cross-asset lead–lag effects are impossible. It rejects this one preregistered BTC feature and filter on ETH/XRP for 2023–2024 under the frozen Bybit execution model. Testing nearby BTC variants after observing this result would violate the preregistration.

## Reproducibility

- Primary and independent repeat runs produced byte-identical result, trade, event-audit, and bootstrap files.
- All output-manifest hashes passed.
- Trade counts, return sums, and TP/SL/timeout rates were independently reconstructed from the compressed trade-level files and matched the summary.
- No policy passed all gates.
- Locked 2025–2026 rows read: **0**.

The next authorized program hypothesis is H3, a separately preregistered order-flow study. It should begin only after authentic source data, provenance checks, the exact feature family, its multiple-testing correction, and a stopping rule are frozen.
