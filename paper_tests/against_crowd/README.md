# Forward paper test: against the crowd at 4% directional changes

Frozen rule (pre-registered 2026-11-14; do not change parameters while the test runs):

- Coins: BTC, ETH, SOL, XRP, DOGE, BNB (Binance USDT perps).
- Event: a 4% directional change on 1-minute closes.
- Crowd = Binance long/short account ratio (log) minus its 7-day mean, read at least 5 minutes before the event.
- Crowd leans hardest against the reversal (top 20% by each coin's pre-2024 history) -> follow it.
  Crowd leans hardest with it (bottom 20%) -> fade it. Exit at the next opposite 4% change.
- Costs: 10 bp per round trip. Forward period starts 2026-11-01.
- Stop early if the mean is below -15 bp after 300 trades. Confirmed only at >= 600 trades with mean > 0 and t > 2.

The workflow `paper-against-crowd` runs daily on Binance's public daily files (one-day delay) and
rewrites `data/paper/against_crowd/trades.csv` and `summary.json`.
