# Crypto Strategy Research: honest backtests on Bybit perpetuals

Four trading ideas tested on real Bybit data, with the test rules written and
frozen **before** any result was computed. Most of them fail, and the reports say
so. That is the point: this repository shows how to tell a real edge from a
backtest artifact.

## Results at a glance

| Study | Idea | Development | Holdout (opened once) | Verdict |
|---|---|---|---|---|
| Trend following | Donchian 20/50/100-day breakout ensemble, 30 perps | Profitable after costs, beat random timing, but not significant and did not beat buy-and-hold | Not opened | Rejected |
| Funding carry | Long spot + short perp, collect funding | 14.2%/yr, max drawdown -1.3% (BTC+ETH) | 3.3%/yr | Real but shrinking premium |
| Volatility premium | Delta-hedged short 30-day straddles priced off Deribit DVOL | 15.0%/yr, passed all gates | 0.7%/yr (base), -7.1%/yr (stress) | Premium has faded |
| Token unlocks | Short before team/investor cliff unlocks | Only 6 verifiable events from free sources | - | Not testable for free |

Full reports: [`reports/`](reports).

**Related study:** [Liquidity-magnet price levels in Bitcoin perpetuals](https://github.com/Crayon99-droid/magnet-price-levels-study),
geometry nulls, mirror placebos and cost-aware entry tests (separate repository and paper,
DOI [10.5281/zenodo.23965420](https://doi.org/10.5281/zenodo.23965420)).

**Working paper:** [From Apparent Profit to a Reproducible Null Result](paper/Directional_Change_Crypto_Null_Result_Tariq_2026.pdf)
(Rayan Tariq, 2026), DOI [10.5281/zenodo.23965288](https://doi.org/10.5281/zenodo.23965288). A directional-change strategy that looked profitable in
TradingView was rebuilt in Python and rejected over the full 2021-2026 history,
then redesigned as a causal event study with regime maps, a dynamic ML threshold
model and family-wise multiple-testing control. `pine/` holds the original script
with its exit-mode diagnostics.

## Method

Every study follows the same discipline:

1. **Pre-registered protocol.** Hypothesis, rules, costs, statistics and pass/fail
   gates are written first and frozen with a SHA-256 fingerprint
   ([`protocols/`](protocols)).
2. **Untouched holdout.** Development data ends 2024-12-31. 2025-2026 is opened once,
   only if development passes, with no rule changes.
3. **Realistic costs.** Taker fees, slippage, historical funding at actual
   timestamps, tick rules; every result is re-run with costs doubled.
4. **Dependence-aware statistics.** Moving-block bootstrap, random-timing placebos,
   Bonferroni correction across variants, comparison with buy-and-hold.
5. **Engine verification.** Each engine is checked against independent
   recomputation (random trades re-derived; ledgers reconciled to 1e-6) and against
   synthetic markets with known answers. Bugs found this way are documented in the
   reports.

## Repository layout

```
protocols/    frozen study protocols + SHA-256 fingerprints
engines/      backtest engines and run scripts (Python, numpy/pandas)
reports/      written results for each study
results/      machine-readable results (JSON)
data_tools/   Bybit + Deribit downloaders with gap/OHLC audits; data fingerprints
unlocks/      token-unlock selection rules, universe, event table with sources
pine/         original TradingView strategy (directional change, exit diagnostics)
paper/        working paper on directional-change trading (PDF)
```

## Reproduce

```bash
pip install requests pandas numpy
python data_tools/bybit_download.py --top 30 --interval 60 --start 2020-01-01
python data_tools/download_round2.py          # spot prices + Deribit DVOL
export BYBIT_DATA=bybit_data
python engines/run_dev.py                     # trend following
python engines/run_carry_dev.py               # funding carry
python engines/run_vrp_dev.py                 # volatility premium
```

Market data is not included; download it with the scripts (Bybit and Deribit
public endpoints, no API key). `data_tools/DATA_MANIFEST.sha256` lists the
fingerprints of the files used in the reports. Note that exchanges can revise
historical data, so a later download may differ slightly.

## Services

I audit and rebuild trading strategies: TradingView/Pine or MT5 logic converted to
Python, checked for look-ahead and repainting, re-tested with realistic fees,
slippage and funding, and judged against placebos and buy-and-hold. Contact via my
GitHub profile.

Not investment advice. Nothing here is a recommendation to trade.
