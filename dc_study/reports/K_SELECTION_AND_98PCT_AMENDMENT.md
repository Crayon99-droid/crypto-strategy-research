# Fixed-k Selection and 98% Inference Amendment

## Corrected conclusion

The methodological concern is valid: `k=1.25` was carried forward because it was the best observed member of nine fixed-threshold streams, but the earlier reports did not apply a joint family-wise correction to that nine-way selection. The 54-cell regime family was protected; the nine standalone fixed-`k` baselines were not.

After applying the same four-calendar-week moving-block max-`t` method to the nine fixed streams, `k=1.25` remains the descriptive winner but **does not establish a positive pre-cost effect at 95% family-wise confidence** in either exit specification.

| Study | Exit rule | `k=1.25` mean before execution cost | Simultaneous 95% lower bound | Adjusted one-sided p | Verdict |
|---|---:|---:|---:|---:|---|
| Phase 4 | 1:1 | +4.1288 bp | −0.1687 bp | 0.0651 | Fails |
| Phase 5 | 1.5:1 | +4.9268 bp | −0.1385 bp | 0.0586 | Fails |

No other multiplier passed in either nine-policy family. Full-cost performance was negative for all nine multipliers; `k=1.25` had simultaneous 95% lower bounds of −14.0803 bp in Phase 4 and −14.0169 bp in Phase 5.

Therefore, the accurate statement is now:

> `k=1.25` was the best observed fixed-threshold candidate, but its modest positive pre-execution mean is not statistically established after correcting for selection among nine multipliers.

It must not be described as a demonstrated latent edge.

## Method

Both Phase 4 and Phase 5 were audited separately because the reward/risk rule changed from 1:1 to 1.5:1.

- Family: all nine fixed multipliers (`0.25` through `3.0`).
- Period: 2023–2024 only.
- Dependence control: four-calendar-week circular moving blocks across 105 consecutive weeks.
- Replications: 10,000.
- Test statistic: maximum studentized mean across all nine policies.
- Confidence: simultaneous family-wise 95% lower bounds.
- Random seed: `20260902`.
- Phase 4 signal-path reconstruction: exactly the frozen Phase 4.1 diagnostic formula.
- 2025–2026 rows read: zero.

The Phase 4 and Phase 5 input hashes and expected row counts were verified before inference. The audit reproduced the previously reported `k=1.25` zero-execution means exactly.

## What happened to the apparent significance?

For Phase 5, the unadjusted `k=1.25` result appeared narrowly positive: the earlier ordinary 95% lower cost-capacity bound was `+1.0311 bp`. Once the fact that `k=1.25` was the winner of nine candidates is incorporated, the simultaneous lower bound becomes `−0.1385 bp` and the adjusted p-value becomes `0.0586`.

This is not a dramatic reversal—the result is just over the boundary—but it is enough that the project must not carry “positive pre-cost edge” forward as an established fact.

## The 98% question

The chronology shows that the 98% rule was not chosen after seeing the cost or selectivity result:

- The saved `EDGE_DISCOVERY_PROGRAM_PLAN.md` predates both later result checkpoints.
- It explicitly allocated one-sided alpha `0.02` to Hypothesis 1 and `0.02` to Hypothesis 2, with `0.01` reserved for Hypothesis 3. Those allocations sum to a 5% program-wide sequential-testing budget.
- The same plan fixed the confirmation-impulse feature, 75th-percentile cutoff, 180-day lookback, minimum 50 prior events, and prohibition on alternative cutoffs before the selectivity outcome was generated.

So 98% was genuinely pre-written for the alpha hypotheses. However, the cost-reality sweep was Workstream 0—a diagnostic gate, not one of those alpha hypotheses. Importing 98% as its primary threshold was unnecessary and made the cost report inconsistent with the Phase 4/5 95% standard.

The correction is:

- Use 95% as the primary cost-diagnostic interval.
- Retain 98% only as a stricter sensitivity result.

This does not change the cost conclusion. Under the 95% calculation:

- Idealized fee-only maker/maker mean: `+0.9258 bp`.
- 95% lower bound: `−2.9699 bp`.
- 95% confidence-adjusted all-in cost capacity: `1.0311 bp`.
- Lowest account fee scenario, maker/maker: `4.0 bp` before any fill drag.

Thus even the lowest fee-only scenario remains outside the confidence-adjusted edge budget.

## Effect on the confirmation-impulse study

The selectivity rule itself was outcome-blind: its feature and cutoff were written in the earlier program plan before its trade outcomes were generated. But its detector stream inherited a retrospectively selected `k=1.25` baseline. Consequently, the selectivity result should be described as a negative diagnostic on the best observed stream—not an independent validation of a previously established fixed-`k` effect.

Because the selectivity hypothesis failed decisively (`−6.6873 bp/trade`, PF `0.892`, 95% lower bound `−14.1123 bp`), this upstream correction does not create or remove a trading claim. It makes the reason for stopping stronger: neither the underlying fixed stream nor its one preregistered price-only filter established a cost-surviving edge.

## Requirement before BTC lead–lag

The next hypothesis must not silently inherit the same nine-way selection bias. Before it runs, its protocol must choose one of two valid protections:

1. Treat all nine detector multipliers as one simultaneous family and apply a joint correction to the absolute BTC-filtered outcomes; or
2. Keep `k=1.25` fixed but evaluate the BTC filter only on a period untouched by the original `k` selection.

No BTC result should be interpreted as evidence until one of these protections is frozen in writing before BTC outcome analysis.
