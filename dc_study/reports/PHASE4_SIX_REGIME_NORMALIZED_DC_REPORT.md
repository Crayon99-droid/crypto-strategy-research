# Phase 4: Six-Regime, Volatility-Normalized Directional Change

## Decision

**NO TRADE.** The corrected regime-first research design did not identify a
single regime–threshold cell that passed the preregistered family-wise evidence
gate. The locked 2025–2026 data was therefore not opened.

This is not a claim that no regime-conditioned threshold can ever work. It is a
more precise result: with the verified ETHUSDT and XRPUSDT Bybit data, the
frozen six-regime taxonomy, nine independent volatility-normalized detector
streams, 1:1 extreme-anchored exits, and conservative Bybit execution model,
the available 2021–2024 evidence is insufficient and the walk-forward economics
are negative.

## Source-data authenticity

The engine used the exact previously downloaded Bybit files, not generated or
interpolated data:

- Eight one-minute candle archives: ETHUSDT and XRPUSDT, 2021–2024.
- Two historical funding-rate files.
- Two Bybit instrument specifications containing tick and quantity rules.
- 3,911,906 one-minute candles in total.

All 12 files matched the SHA-256 values in the original download audit. Every
candle archive passed ZIP CRC, schema, row-count, coverage, one-minute
chronology, duplicate-timestamp, finite-numeric, and OHLC-invariant checks.
No EURUSD attachment or synthetic price record was used.

The causal collection produced 388,338 usable candidates:

| Symbol | One-minute bars | Candidates |
|---|---:|---:|
| ETHUSDT | 1,998,720 | 199,356 |
| XRPUSDT | 1,913,186 | 188,982 |
| **Total** | **3,911,906** | **388,338** |

## Corrections implemented

### 1. Small, explicit regime taxonomy

The taxonomy is limited to two dimensions:

| Dimension | States | Causal definition |
|---|---|---|
| Volatility level | Low / Normal / High | Previous completed UTC day's median 240-minute realized volatility, ranked against a trailing 180-day history after 30 days of warm-up |
| Price structure | Range / Trend | Hysteretic state based on \(\lvert r_{240}/RV_{240}\rvert\): enter trend at 0.75, return to range at 0.25 |

This creates only six cells: low-range, low-trend, normal-range,
normal-trend, high-range, and high-trend. Long and short are served by one
policy and are not separate regime dimensions.

### 2. Smoothed, causal dynamic threshold

The threshold for multiplier \(k\) on day \(t\) is

\[
\theta_t(k)=k\,\sigma_t,
\]

where \(\sigma_t\) is the previous completed UTC day's median of rolling
240-minute realized volatility. It changes only once per day. The tested
multipliers were:

\[
k\in\{0.25,0.5,0.75,1,1.25,1.5,2,2.5,3\}.
\]

The same causal volatility estimate drives both threshold scaling and the
volatility regime, avoiding inconsistent volatility definitions.

### 3. Independent directional-change streams

Each multiplier is a separate detector with its own state, running high/low,
event sequence, entries, and exits. A larger threshold is not a retrospective
label applied to a smaller-threshold event.

For this baseline, the pivot and reversal threshold parameters are explicitly
represented but coupled to the same \(\theta_t(k)\). This is intentional and
recorded in the configuration; later work could decouple them only as a new,
preregistered experiment.

The detector uses bar highs/lows for the running extreme and the bar close for
reversal confirmation. After confirmation, the new leg begins at the signal
bar close, avoiding an unknowable intrabar high/low ordering assumption.

### 4. Threshold-consistent trade definition

Each candidate is its own hypothetical trade:

- Entry: next one-minute bar open, with adverse market slippage and tick rounding.
- Stop: that detector stream's running extreme at confirmation.
- Target: one entry-to-extreme risk unit (1:1 reward/risk).
- Maximum hold: 240 minutes.
- Same-minute stop and target: stop first.
- Regime changes while open: the original stop, target, and time exit remain frozen.

The Bybit model includes 5.5 bp taker commission on each fill notional,
adverse slippage and tick rounding on both fills, and actual historical funding
settlements. Funding uses entry notional because a historical Bybit mark-price
series was not present; no mark prices were invented.

### 5. Strict expanding walk-forward design

| Test fold | Training information available |
|---|---|
| 2023 H1 | Trades completed before 2023-01-01 |
| 2023 H2 | Trades completed before 2023-07-01 |
| 2024 H1 | Trades completed before 2024-01-01 |
| 2024 H2 | Trades completed before 2024-07-01 |

Threshold streams are made non-overlapping before their trades are attributed
to regimes. This prevents one threshold stream from holding mutually
impossible simultaneous positions in different regime cells.

Within each training fold, a regime can select a multiplier only if it passes
all of these frozen gates:

- At least 200 non-overlapping trades.
- At least 26 calendar weeks.
- At least four training half-years and 30 trades in every half-year.
- At least 75% positive evaluated half-years.
- At least 50 ETH and 50 XRP trades.
- At least 50 long and 50 short trades.
- Profit factor above 1.
- Positive simultaneous family-wise 95% lower confidence bound.

The confidence bound comes from a 10,000-replicate, four-calendar-week moving
block, studentized max-\(t\) bootstrap. The same resampled time blocks are used
across all 54 regime × multiplier cells in a fold. This respects time
dependence and controls the family-wise error caused by searching many cells.
The resampling design follows the book's bootstrap emphasis (pp. 211–212),
while the untouched forward periods serve the out-of-sample control advocated
in its backtesting discussion (around p. 236). Transaction costs are included
before inference, consistent with the book's transaction-cost chapter.

## Statistical result

Across four folds there were 216 training cells. Thirty-two had a positive
nominal historical mean. **Zero passed the full evidence gate.** Therefore the
evidence policy mapped every regime to no trade and placed zero trades.

The strongest nominal cell illustrates the multiple-testing problem:

| Training fold | Regime | Multiplier | Trades | Mean net | PF | Family-wise p | Simultaneous lower 95% |
|---|---|---:|---:|---:|---:|---:|---:|
| 2023 H1 | High volatility / range | 2.0 | 97 | +53.59 bp | 1.510 | 0.638 | −34.80 bp |

It had fewer than the 200 required observations, inadequate half-year coverage,
and a negative family-wise lower bound. Treating it as a discovered "true
threshold" would be data snooping.

## Walk-forward economics

The forced regime policy is diagnostic only: it chooses the highest historical
mean in each regime even when the evidence gate fails.

| Policy | Trades | Mean net/trade | Profit factor | Positive half-year folds |
|---|---:|---:|---:|---:|
| Evidence-gated regime map | 0 | — | — | — |
| Forced six-regime map | 3,923 | −14.42 bp | 0.751 | 0/4 |
| Best fixed baseline, \(k=1.25\) | 5,667 | −9.77 bp | 0.820 | 0/4 |

Forced-map folds:

| Fold | Trades | Mean net/trade | Profit factor |
|---|---:|---:|---:|
| 2023 H1 | 1,021 | −12.51 bp | 0.758 |
| 2023 H2 | 961 | −15.85 bp | 0.726 |
| 2024 H1 | 1,167 | −11.03 bp | 0.800 |
| 2024 H2 | 774 | −20.26 bp | 0.711 |

The forced multiplier repeated from one half-year map to the next in only
11 of 18 adjacent regime comparisons (61.1%). That is some persistence, but it
did not translate into positive out-of-sample returns.

## Why longs looked better than shorts

The policy remains unified; this is a diagnostic, not a proposal for separate
models.

| Side | Trades | Gross after slippage | Fee | Funding | Net |
|---|---:|---:|---:|---:|---:|
| Long | 1,841 | +4.46 bp | −11.00 bp | −0.37 bp | −6.91 bp |
| Short | 2,082 | −10.45 bp | −11.01 bp | +0.40 bp | −21.06 bp |

Funding slightly helped shorts and hurt longs, so funding does not explain the
gap. The main difference is price-path behavior after entry. Short timeouts
lost −33.63 bp gross on average, versus −5.41 bp for long timeouts.

The underlying development period had strong positive drift:

| Symbol | 2023 | 2024 |
|---|---:|---:|
| ETHUSDT | +90.87% | +46.04% |
| XRPUSDT | +81.49% | +238.28% |

The sign-agnostic trend/range dimension treats bullish and bearish structure
as equivalent. In a strongly rising sample, that made long reversals less bad
than short reversals. This is exposure to drift/trend alignment, not evidence
that the directional-change detector predicted better on one side.

One post-hoc subgroup—forced high-volatility/trend longs—was positive at
+9.20 bp over 190 trades, while shorts in the same cell lost −35.00 bp over 224
trades. It cannot be promoted into a rule after seeing the result; doing so
would repeat the multiple-comparison error this phase was built to prevent.

## Interpretation and next hypothesis

The user's central formulation was implemented correctly: identify the market
regime first, then identify a threshold supported in that regime, otherwise do
not trade. The empirical answer for this specification is **no trade in every
regime**.

If research continues, the most defensible single change is to replace the
sign-agnostic trend/range dimension with a symmetric alignment dimension:
signal aligned with the prevailing 240-minute trend versus signal opposing it.
That would still be one long/short policy, not separate side models. It should
replace an existing dimension rather than expand the grid, and it must be
preregistered and walk-forward-tested with the same family-wise gate. No other
parameters should change in that experiment.

## Limitations

- One-minute OHLC cannot reveal intrabar path; same-minute ties are stop-first.
- Historical mark price was unavailable, so funding notional uses entry price.
- Fee/slippage assumptions are conservative but not a historical order-book replay.
- Two assets provide limited independent market history even with many minute bars.
- The six-regime taxonomy is a testable modeling choice, not a natural law.

## Integrity and reproducibility

The full engine was run twice. All principal analytical CSV and compressed
trade-table hashes matched byte for byte. The checkpoint verifier checks source
hashes, SQLite integrity, row counts, threshold arithmetic, trade geometry,
cost accounting, locked-period exclusion, result reconciliation, and artifact
hashes.
