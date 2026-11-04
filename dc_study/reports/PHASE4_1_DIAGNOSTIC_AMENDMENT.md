# Phase 4.1 Diagnostic Amendment

## Corrected conclusion

The Phase 4 conclusion must be stated narrowly:

> **No regime–threshold cell was tradable under the frozen 1:1,
> extreme-anchored, 240-minute, full-cost Phase 4 rule.**

Phase 4 did **not** test 1.5:1 or 2:1 reward/risk, trailing exits, alternative
holding periods, or every possible regime definition. It therefore cannot
support the broader claim that directional change is untradeable under all exit
rules or that threshold-confirmed reversals contain no directional information.

This amendment is diagnostic only. It did not change or reselect a single
Phase 4 trade, threshold, regime, stop, target, or holding period. The
2025–2026 data remains unopened.

## Exit-reason mechanism

### Walk-forward policies

| Policy | Trades | TP first | SL first | Timeout | TP rate | SL rate | Timeout rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| Forced six-regime map | 3,923 | 1,133 | 1,030 | 1,760 | 28.88% | 26.26% | 44.86% |
| Fixed \(k=1.25\) | 5,667 | 1,713 | 1,509 | 2,445 | 30.23% | 26.63% | 43.14% |

The strategy did not lose simply because stop-outs outnumbered targets; TP hits
actually exceeded SL hits. The unresolved timeout bucket was large and
economically important.

| Policy / outcome | Trades | Median exit | Mean signal-path gross | Mean full-cost net |
|---|---:|---:|---:|---:|
| Forced map — TP | 1,133 | 59 min | +136.08 bp | +122.30 bp |
| Forced map — SL | 1,030 | 74.5 min | −122.02 bp | −135.81 bp |
| Forced map — timeout | 1,760 | 240 min | −17.47 bp | −31.39 bp |
| Fixed \(k=1.25\) — TP | 1,713 | 60 min | +139.74 bp | +125.94 bp |
| Fixed \(k=1.25\) — SL | 1,509 | 71 min | −131.95 bp | −145.80 bp |
| Fixed \(k=1.25\) — timeout | 2,445 | 240 min | −7.02 bp | −20.88 bp |

This is not merely death by fees on flat timeouts. Before costs, timeouts were
already adverse on average, especially under the forced regime map. Costs then
made that unresolved bucket materially worse.

### All 216 training cells

The new cell-level table reports TP, SL, and timeout counts/rates, median exit
time, outcome-specific returns, and full/half/zero-cost returns for all 54
regime × multiplier cells in each of four folds.

- Median timeout rate across the 216 training cells: **48.04%**.
- Cells with timeout rate above 50%: **107/216**.
- Cells with positive signal-path gross mean: **146/216**.
- Cells still positive after half execution costs: **55/216**.
- Cells positive after full Phase 4 costs: **32/216**.

These counts are descriptive, not new hypothesis tests. The cells overlap
across expanding training folds and were already part of the searched Phase 4
family.

## Cost problem versus signal problem

The diagnostic scenarios preserve the exact Phase 4 trade geometry and trade
selection:

- **Signal-path gross:** raw next-bar open to the un-slipped frozen exit
  reference, without slippage, fees, or funding.
- **Half execution cost:** 50% of the observed slippage/tick cost and fee,
  with historical funding unchanged.
- **Zero execution cost:** no slippage or fees, with historical funding
  unchanged.
- **Full cost:** the actual Phase 4 assumption.

| Policy | Signal-path gross | Signal PF | Half-cost net | Half-cost PF | Full-cost net | Full-cost PF |
|---|---:|---:|---:|---:|---:|---:|
| Forced six-regime map | −0.57 bp | 0.989 | −7.47 bp | 0.862 | −14.42 bp | 0.751 |
| Fixed \(k=1.25\) | **+4.07 bp** | **1.087** | −2.82 bp | 0.944 | −9.77 bp | 0.820 |

The two failures have different mechanisms:

1. **Forced regime selection is a signal problem.** It was negative even before
   execution costs. The tested regime lookup did not improve the underlying
   directional-change stream.
2. **Fixed \(k=1.25\) has a small cost-sensitive signal.** It was modestly
   positive before costs but not large enough to survive realistic Bybit
   execution. Half of the modeled execution cost was still enough to make it
   negative.

The fixed stream's signal-path result by fold was:

| Fold | Trades | Signal gross | Signal PF | Full-cost net | Full-cost PF |
|---|---:|---:|---:|---:|---:|
| 2023 H1 | 1,342 | +3.07 bp | 1.072 | −11.19 bp | 0.775 |
| 2023 H2 | 1,653 | −0.34 bp | 0.992 | −14.05 bp | 0.713 |
| 2024 H1 | 1,313 | +4.78 bp | 1.096 | −8.95 bp | 0.842 |
| 2024 H2 | 1,359 | +9.76 bp | 1.174 | −3.93 bp | 0.938 |

This is not a proven latent edge: one fold was negative and no new confidence
claim is made. It does, however, mean the accurate diagnosis is not “the signal
contains nothing.” It is “the modest pre-cost effect does not cover the tested
execution and exit structure.”

## The 240-minute limitation

The four-hour hold was a frozen research assumption, not an empirically
established optimum. The descriptive resolution curve for fixed \(k=1.25\) is:

| Horizon | TP resolved | SL resolved | Still unresolved |
|---|---:|---:|---:|
| 15 min | 363 | 273 | 88.78% |
| 30 min | 541 | 411 | 83.20% |
| 60 min | 857 | 674 | 72.98% |
| 120 min | 1,237 | 1,045 | 59.73% |
| 240 min | 1,713 | 1,509 | 43.14% |

This table does **not** constitute a causal holding-period sensitivity test.
Changing the maximum hold changes timeout prices, position occupancy, and which
later signals can become trades. A valid 60/120/480-minute comparison requires
rerunning the policy and treating the added horizons as another
multiple-comparison family.

## Long/short section status

Every long/short figure in Phase 4 came from the rejected forced-map policy.
It remains a post-hoc description of that rejected sample and cannot justify a
trend-alignment filter, directional regime, or separate long/short model.
Accordingly, the previously suggested trend-alignment reformulation is removed
from the immediate research plan.

## Cross-phase stopping rule

The rule was written before calculating the Phase 4.1 diagnostics:

1. Phase 4.1 cannot select a model.
2. At most **one** further preregistered reformulation may be run before 2025.
3. Permission is based only on fixed \(k=1.25\), not the rejected forced map.
4. It requires positive signal-path gross in at least three of four folds,
   positive aggregate gross mean, and aggregate signal-path PF above 1.
5. If permitted, Phase 5 may change only reward/risk from 1:1 to **1.5:1**.
   The six regimes, nine threshold streams, 240-minute hold, unified long/short
   policy, costs, and family-wise inference remain unchanged.
6. Phase 5 is the final pre-2025 reformulation. Failure terminates this
   directional-change reversal research line.
7. 2025 is opened once only if a frozen Phase 5 policy passes development.
   2026 is opened only if it subsequently passes the preregistered 2025 gate.

Observed permission result:

- Positive signal-path folds: **3/4**.
- Aggregate signal-path mean: **+4.07 bp**.
- Aggregate signal-path PF: **1.087**.
- Decision: **one final Phase 5 is permitted**.

This permission is not a claim of profitability. It allows one narrowly defined
test of whether a larger reward relative to the same extreme-anchored risk can
convert the modest pre-cost effect into a cost-surviving result. There will be
no subsequent reshuffling if it fails.

## Trade-level verification

The amendment includes:

- Every one of the 1,167 forced-regime trades in the 2024 H1 fold as an
  uncompressed CSV.
- Every policy trade in 2024 H1 as a compressed CSV.
- Original signal, extreme, regime, dynamic threshold, entry, stop, target,
  exit, outcome, holding time, slippage, fee, funding, and net-return fields.
- Added exit-reference, signal-path gross, slippage/tick cost, half-cost, and
  zero-cost diagnostic fields.

These extracts allow the reported narrative to be checked directly against
the trade records rather than accepted from summary statistics alone.
