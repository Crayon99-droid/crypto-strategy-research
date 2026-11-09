# Deribit hourly options recorder (Helios Macro)
GitHub Actions runs `snapshot.py` every hour at :02 UTC (`.github/workflows/deribit-snapshot.yml`) and commits to `data/deribit/`:
- `YYYY/MM/DD/HH_<BTC|ETH>_book.csv.gz` – every listed option: open interest, mark IV, bid/ask, underlying, 24h volume, BS gamma
- `YYYY/MM/DD/HH_<BTC|ETH>_trades.csv.gz` – all option trades in the previous hour (buy/sell direction, size, IV, block-trade flag)
- `summary.csv` – one row per currency per hour: spot, call/put OI, call/put gamma exposure (USD per 1% move), top-OI strike, flow totals
Purpose: build the history needed to backtest dealer-gamma regimes and options flow, which Deribit's free API does not provide retroactively.
About 1.5 MB per day. Run manually from the Actions tab → deribit-hourly-snapshot → Run workflow.
