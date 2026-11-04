# Pre-registered protocol: crypto volatility risk premium screen (v1)

Status: FROZEN 2026-11-05 (UTC+5), written before the DVOL data was downloaded
and before any result was computed. SHA-256 in PROTOCOL_VRP_v1.sha256.

## 1. Hypothesis
Deribit BTC and ETH options are priced at implied volatility above the volatility
later realised (literature: BTC variance risk premium ~14%/yr at one month,
Deribit 2017-2022, arXiv 2410.15195). A seller who delta-hedges keeps a positive
part of that premium after realistic option and hedging costs.

This is a SCREEN. Real historical option quotes are not available to us. A pass
means "worth buying real option data and testing a defined-risk structure", not
"tradeable".

## 2. Data and split
- Deribit DVOL (30-day implied-volatility index), hourly, BTC and ETH.
- Underlying: Bybit USDT-perp hourly candles (proxy for Deribit's index).
- Development: DVOL start to 2024-12-31. Only option cycles whose 30-day expiry is
  on or before 2024-12-31 are used, so no realised path enters the holdout.
- Holdout: cycles sold from 2025-01-01 whose expiry is inside the data.

## 3. Part A: premium measurement
Each day at 08:00 UTC: IV = DVOL value at that hour. RV = realised volatility over
the next 720 hours from hourly log returns, annualised (x sqrt(8760/720) applied to
the root sum of squares). Report IV-RV (vol points) and IV^2-RV^2: mean, median,
share of days positive, worst 1% outcome, by year. Descriptive only.

## 4. Part B: stylised backtest (decisive)
- Each day at 08:00 UTC sell one 30-day at-the-money straddle (call + put, strike =
  underlying at 08:00) sized at 1/30 unit of underlying, so about one unit is
  outstanding at steady state. Black-Scholes, rate 0, flat smile.
- Sale implied volatility = DVOL - 2 vol points. DVOL is a variance-swap-style index
  built from all strikes and sits above at-the-money IV; the 2-point haircut is a
  fixed conservative assumption, stressed below.
- Mark open options daily at 08:00 at (DVOL - 2) with the remaining time.
- Delta hedge once a day at 08:00 with the perpetual to zero net delta.
- Expiry after 30 days at 08:00: pay intrinsic value.
- Costs:
  - option fee per leg min(0.03% of underlying, 12.5% of premium), at sale;
  - settlement fee per leg min(0.015% of underlying, 12.5% of option value);
  - bid-ask: half-spread of 1.0 vol point x vega, at sale;
  - hedge trades: 0.055% fee + 0.05% slippage on traded notional;
  - funding on the hedge position at actual rates.
- Returns on reference capital 1.0 = one unit of underlying notional at the day's price.
- Equal weight BTC and ETH.

Stress: sale IV = DVOL - 4 vol points AND half-spread 2.0 vol points.

## 5. Statistics
Daily P&L series; moving-block bootstrap, 30-day blocks, 10,000 reps, one-sided
alpha 0.025. Reported: annual return, Sharpe, max drawdown, worst day, worst 30 days,
by year, by coin, P&L split into premium, option payoff, hedge, costs, funding.

## 6. Decision rules
Development PASS (BTC+ETH combined) requires all of:
1. Bootstrap lower bound on mean daily net P&L > 0.
2. Net P&L > 0 under the stress setting.
3. Positive in at least 3 of the 4 calendar years 2021-2024.
4. Max drawdown < 3x annual mean net P&L.
Holdout PASS: net > 0 and max drawdown < 1.5x development maximum.
PASS leads only to buying real option data. FAIL: report the null.
