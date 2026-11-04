# Fixed k=1.25 cost-sensitivity gate

## Decision

Cost reduction alone cannot establish the unconditional fixed-(k=1.25) stream as a tradable edge on this account.

The next authorized step is the preregistered confirmation-impulse selectivity hypothesis. Maker execution remains a separate data problem; this cost gate does not assume maker orders fill.

## Data and integrity

- Input: the frozen Phase 5 walk-forward trade file.
- Input SHA-256: `853f554466e6d6892304774aba28562b9f2d78b02862039da4eb8700172f9a8f` (exact manifest match).
- Policy: fixed (k=1.25), 1.5R exit, 240-minute maximum hold.
- Trades: 5,187 across 105 consecutive calendar weeks.
- Coverage: four half-years, ETHUSDT/XRPUSDT, both long and short.
- Historical funding and all original trade timestamps were retained.
- Locked 2025–2026 rows read: zero.
- The account taker/taker calculation reproduced every original Phase 5 trade with maximum error below (10^{-9}) bp.

## Account rates tested

- Standard futures maker: 2 bp per fill.
- Standard futures taker: 5.5 bp per fill.
- MNT discount: off.
- Innovation Zone rates were excluded because this gate concerns ETH/XRP.

Leverage and cross/isolated margin do not change these fee rates; fees are calculated from position notional.

## Result by execution scenario

| Scenario | Non-fee drag | Mean net | PF | 95% lower bound | 98% one-sided lower bound | Interpretation |
|---|---:|---:|---:|---:|---:|---|
| Taker/taker, existing fills | Existing | -8.97 bp | 0.843 | -12.84 bp | -13.00 bp | Executable baseline fails |
| Taker/taker, fee only | Zero | -6.08 bp | 0.890 | -9.97 bp | -10.13 bp | Fails even with impossible zero slippage |
| Maker/taker, existing fills | Existing | -5.47 bp | 0.901 | -9.34 bp | -9.50 bp | Fee sensitivity only; fails |
| Maker/taker, fee only | Zero | -2.58 bp | 0.952 | -6.47 bp | -6.63 bp | Fails even before fill risk |
| Maker/maker, existing fill geometry | Existing | -1.97 bp | 0.963 | -5.84 bp | -6.00 bp | Negative |
| Maker/maker, fee only | Zero | **+0.93 bp** | 1.018 | -2.97 bp | -3.13 bp | Positive mean only; statistically and economically fails |

The maker-labelled rows are mathematical fee substitutions. They are not claims that a post-only order would have filled at the original price or time.

## Edge budget

The frozen stream's zero-execution-cost mean, including historical funding, is:

\[
4.9268\text{ bp per trade}
\]

This is the mean break-even all-in cost. It is not a reliable cost allowance because the underlying return varies substantially by time block.

Four-week moving-block results:

- Mean break-even cost: 4.9268 bp.
- 95% lower-bound cost capacity: **1.0311 bp**.
- Preregistered 98% one-sided lower-bound cost capacity: **0.8747 bp**.

On the 0.25 bp cost grid:

- At 0.75 bp total cost, the 98% lower bound remains +0.1247 bp.
- At 1.00 bp total cost, it becomes -0.1253 bp.
- At 4.00 bp—the idealized maker/maker fee before adverse selection—the mean is +0.9268 bp, but the 98% lower bound is -3.1253 bp.
- At 5.00 bp, even the mean becomes negative.

No real execution mode available to this account fits within the confidence-adjusted 0.8747 bp capacity.

## Stability diagnosis

Even the idealized maker/maker upper bound was positive in only two of four half-years:

| Half-year | Ideal maker/maker mean | PF |
|---|---:|---:|
| 2023 H1 | +3.62 bp | 1.081 |
| 2023 H2 | -4.25 bp | 0.909 |
| 2024 H1 | -0.29 bp | 0.995 |
| 2024 H2 | +5.48 bp | 1.090 |

It was also inconsistent by asset and side. The largest weakness was XRP shorts at -7.58 bp even under ideal fee-only maker/maker assumptions. The strongest subgroup was XRP longs at +8.65 bp, but this is a diagnostic subgroup—not authorization for a separate model.

## Research implication

The signal contains a small pre-cost effect, and its block-bootstrap lower bound is positive at zero cost. The problem is that the effect is too small and unstable to pay even the account's best standard fee schedule with adequate statistical protection.

Therefore:

1. Do not spend resources attempting to rescue the unconditional stream through fee tier changes alone.
2. Do not interpret the +0.93 bp ideal maker/maker mean as an executable edge.
3. Run the single preregistered confirmation-impulse filter to test whether the gross effect can be concentrated into fewer, higher-quality entries.
4. Consider maker/L2 work only if a selected signal first creates enough gross expectancy to justify fill research.

## Gate outcome

`COST_GATE_FAIL_UNCONDITIONAL_STREAM__PROCEED_TO_CONFIRMATION_IMPULSE_SELECTIVITY`
