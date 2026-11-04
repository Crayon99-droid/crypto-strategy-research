# Phase 5: Final RR 1.5 Directional-Change Test

## Decision

**NO TRADE — TERMINATE THIS RESEARCH LINE.**

The single final pre-2025 reformulation changed reward/risk from 1:1 to 1.5:1
and changed nothing else. Zero of the 54 final through-2024
regime–threshold cells passed the unchanged evidence gate. The evidence-gated
2025 map is therefore no trade in all six regimes, and 2025–2026 remains
unopened.

This terminates the standalone, price-only, market-taker directional-change
reversal specification. It does not establish that no trading edge exists in
maker execution, order flow, open interest, cross-asset information, or other
untested data.

## The central hypothesis failed

The original project hypothesis was that identifying the regime first would
allow the system to select a better threshold than one fixed multiplier.
Phase 5 again produced the opposite result:

| Walk-forward policy | Trades | Signal-path gross | Full-cost net | Full-cost PF |
|---|---:|---:|---:|---:|
| Forced regime-conditioned map | 2,541 | −6.16 bp | −20.04 bp | 0.717 |
| Fixed \(k=1.25\) | 5,187 | **+4.86 bp** | **−8.97 bp** | **0.843** |
| Regime selection minus fixed | — | **−11.02 bp** | **−11.07 bp** | — |

The regime map did not merely fail to prove an improvement. It selected
thresholds whose subsequent price paths were materially worse than the
preregistered fixed baseline before execution costs. The regime-first
threshold-selection hypothesis is therefore unsupported by this experiment.

## Frozen specification

Phase 5 was written and hashed before its outcomes were calculated. Relative
to Phase 4, the only research parameter change was:

\[
RR: 1.0 \longrightarrow 1.5.
\]

The following remained unchanged:

- ETHUSDT and XRPUSDT Bybit linear perpetuals.
- Verified one-minute candles and historical funding through 2024.
- Six regimes: volatility level × trend/range structure.
- Nine independent volatility-normalized threshold streams.
- Stop at the detector's running extreme.
- Next-minute market entry and conservative market exit.
- 240-minute maximum hold and stop-first same-minute ties.
- Unified long/short handling.
- Fees, slippage, tick rounding, and funding.
- Four expanding walk-forward folds.
- The 54-cell, 10,000-replicate moving-block max-\(t\) family-wise gate.

Maker orders, trade filters, order-book variables, alternative holding periods,
and new regime definitions were not tested.

## Final statistical result

The final evidence calculation used every permitted 2021–2024 observation and
the unchanged simultaneous family-wise inference:

| Result | Value |
|---|---:|
| Final regime × threshold cells | 54 |
| Cells with positive mean net return | **0** |
| Cells with PF above 1 | **0** |
| Cells with positive simultaneous lower bound | **0** |
| Complete evidence-gate passes | **0** |

The least-negative final cell was low-volatility/range with \(k=2.5\):

| Trades | Mean net | PF | Simultaneous lower 95% | Positive half-years |
|---:|---:|---:|---:|---:|
| 404 | −0.36 bp | 0.993 | −20.24 bp | 3/7 |

It failed economically, statistically, and on time stability. It cannot be
promoted into a candidate because it happened to be closest to zero.

## What changing RR actually did

| Policy | RR | TP rate | SL rate | Timeout rate | Signal path | Half-cost | Full cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Forced regime map | 1.0 | 28.88% | 26.26% | 44.86% | −0.57 bp | −7.47 bp | −14.42 bp |
| Forced regime map | 1.5 | 9.45% | 17.24% | **73.32%** | −6.16 bp | −13.08 bp | −20.04 bp |
| Fixed \(k=1.25\) | 1.0 | 30.23% | 26.63% | 43.14% | +4.07 bp | −2.82 bp | −9.77 bp |
| Fixed \(k=1.25\) | 1.5 | 17.43% | 27.30% | **55.27%** | +4.86 bp | −2.02 bp | −8.97 bp |

The wider target reduced completed target hits and increased unresolved trades,
as anticipated. It slightly improved the fixed baseline's pre-cost and
full-cost means, but the improvement was far too small to become tradable. It
made the forced regime policy substantially worse.

For fixed \(k=1.25\), the 1.5:1 outcome mechanism was:

| Outcome | Trades | Mean signal path | Mean full-cost net | Median exit |
|---|---:|---:|---:|---:|
| TP first | 904 | +203.94 bp | +190.18 bp | 81 min |
| SL first | 1,416 | −133.38 bp | −147.22 bp | 78 min |
| Timeout | 2,867 | +10.36 bp | **−3.49 bp** | 240 min |

The fixed stream's timeouts were modestly favorable before execution costs but
negative afterward. This is a cost-sensitive, low-magnitude price effect—not a
cost-surviving edge. The average modeled execution drag was approximately
13.90 bp per trade.

## Fold stability and the permission-gate caveat

| Fold | Trades | Signal path | Half-cost | Full-cost | Full PF |
|---|---:|---:|---:|---:|---:|
| 2023 H1 | 1,242 | +7.55 bp | +0.46 bp | −6.71 bp | 0.867 |
| 2023 H2 | 1,475 | −0.29 bp | −7.13 bp | −14.00 bp | 0.733 |
| 2024 H1 | 1,208 | +3.59 bp | −3.20 bp | −10.11 bp | 0.832 |
| 2024 H2 | 1,262 | +9.43 bp | +2.62 bp | −4.24 bp | 0.936 |

The earlier permission rule counted three positive pre-cost folds. These are
adjacent half-years from the same two correlated assets, not four independent
confirmations. Phase 5 honored that prewritten permission rule, but the result
should not be interpreted as though three independent markets confirmed an
edge. Every fold remained negative after full costs.

## What failure means—and does not mean

The correct conclusion is:

> Changing the target from 1:1 to 1.5:1 did not rescue the frozen price-only,
> market-taker DC-reversal strategy, and regime-conditioned threshold selection
> again underperformed fixed \(k=1.25\).

It is not correct to conclude:

- that maker execution was tested;
- that a realistic limit order would necessarily fill;
- that selective fixed-threshold trading was tested;
- that order flow, order-book imbalance, open interest, basis, or BTC lead–lag
  contains no information;
- or that no trading edge exists anywhere.

A simple 50%-execution-cost diagnostic still left fixed \(k=1.25\) negative at
−2.02 bp/trade. Therefore “use maker orders” is a research hypothesis, not an
automatic rescue. A valid maker study requires historical bid/ask or order-book
data, queue/fill assumptions, missed-order accounting, and adverse-selection
measurement. One-minute OHLC cannot truthfully supply those facts.

## Research decision after Phase 5

There will be no Phase 6 that reshuffles this price-only regime taxonomy. The
next defensible work must be a new research program with a new preregistration
and authentic additional data:

1. **Execution feasibility:** measure post-only/limit fill probability and
   realized adverse selection from historical Bybit market microstructure data.
2. **Selectivity on a fixed detector:** treat fixed \(k=1.25\) DC events as a
   candidate feature and require an independently defined conviction filter;
   do not return to regime-based threshold lookup.
3. **Non-price information:** evaluate order-flow imbalance, open interest,
   funding/basis extremes, and BTC-to-ETH/XRP lead–lag.
4. **DC as one feature:** combine the event with independently sourced market
   information rather than treating threshold reversal as a complete strategy.

These are genuinely untested levers. They must not be evaluated on 2025 merely
because it is available; any new program needs its own training/validation/test
split so that a fresh final holdout remains untouched.

## Data and reproducibility audit

- All 12 original Bybit source files again matched their SHA-256 audit values.
- All eight candle ZIPs passed CRC, schema, row count, chronology, duplicate,
  finite-numeric, and OHLC checks.
- 3,911,906 authentic one-minute candles were checked.
- Phase 5 regenerated 388,338 candidates.
- All 388,338 candidate IDs and every stable detector, signal, extreme, regime,
  entry, and stop field matched Phase 4 exactly.
- Every target was 1.5 risk units from entry within one instrument tick.
- An independent full rerun reproduced every principal analytical table and
  the compressed 96,211-row walk-forward trade file byte for byte.
- SQLite integrity passed.
- Price rows read from 2025–2026: **0**.

The result is negative, but it is a genuine result from verified market data,
not generated, interpolated, or retrospectively relabeled price history.
