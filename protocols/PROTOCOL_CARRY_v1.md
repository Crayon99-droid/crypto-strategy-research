# Pre-registered protocol: funding-rate carry (v1)

Status: FROZEN 2026-11-05 (UTC+5), written before the spot data was downloaded and
before any carry result was computed. SHA-256 in PROTOCOL_CARRY_v1.sha256.
Prior exposure: funding-rate averages per coin were seen during the data audit and
in the TSMOM v1 report (funding as a cost). No carry P&L has been computed.

## 1. Hypothesis
Leveraged longs on Bybit perpetuals pay persistent positive funding. A position that
is long spot and short the perpetual, in equal units, collects that funding with
little price exposure. Net of fees, slippage and basis changes, it earns a positive
return with low drawdown. This is a risk premium, not a prediction.

## 2. Data and split
- Bybit spot hourly candles, Bybit USDT-perp hourly candles, Bybit funding history
  (actual timestamps). SHIB1000USDT perp is paired with SHIBUSDT spot x 1000.
- Development: first date with spot, perp and funding all present (+7-day warm-up)
  to 2024-12-31. Holdout: 2025-01-01 to the end of the data, opened once.
- Decisive universe: BTC+ETH. Supporting: every coin of the 30 with a Bybit spot pair
  (survivorship-biased, as before).

## 3. Rules
Signal, evaluated daily at 00:00 UTC per coin:
  F7 = sum of funding rates paid in the last 7 days x 365/7 (annualised).
- Enter when F7 > 15% (flat beforehand).
- Exit when F7 < 0%.
- Fills: both legs at the 00:00 hourly open after the signal (spot open for spot,
  perp open for perp), plus slippage.
- Equal units: buy u = notional / spot price; short the same u on the perp.
- Notional per coin = 1 / N_coins of reference capital 1.0. No resizing while open.
- Funding received = u x perp price at the funding hour x rate (negative rates are paid).
- End of a data segment: forced exit at the last close, with costs.

## 4. Variants (complete family)
1. Primary: the conditional rule above.
2. Secondary: always-on carry (enter at the first date, exit at the end).
One-sided alpha 0.025 each (Bonferroni over 2).

## 5. Costs
Spot fee 0.10% per fill; perp taker fee 0.055% per fill; slippage 0.05% per fill
per leg. Round trip on both legs = 0.51% of notional.
Stress: fees and slippage doubled.
Not modelled: spot-collateral haircut and borrowing; reported as limits.

## 6. Statistics
Daily net returns on reference capital; moving-block bootstrap, 20-day blocks,
10,000 reps. Reported: annual return, volatility, Sharpe, max drawdown, worst day,
time in market, trades, funding received, fees, basis P&L, by year and by coin.
Risk check (report only): largest unrealised loss on the perp leg during any hold,
as a fraction of that coin's notional.

## 7. Decision rules
Development PASS (BTC+ETH, per variant) requires all of:
1. Bootstrap lower bound on mean daily net return > 0.
2. Net return > 0 with doubled costs.
3. Positive net return in at least 75% of calendar years with data.
4. Max drawdown < 10% of capital.
5. Annual net return > 5% (economic hurdle: below this, a stablecoin deposit
   pays similar money without exchange-execution risk).
Holdout PASS: net return > 0 and max drawdown < 1.5x development maximum.
Fail: report the null; no tuning of thresholds on the same data.
