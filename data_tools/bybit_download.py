#!/usr/bin/env python3
"""
Bybit V5 public-data downloader for strategy research.

Downloads, for USDT linear perpetuals:
  * OHLCV klines (any interval: 1, 5, 15, 60, 240, D ...)
  * funding-rate history
  * instrument metadata (tick size, qty step, launch time)
  * optional: raw public trades (aggressor side) from public.bybit.com

No API key is needed. Only Python 3.9+ and `requests` (pip install requests).
pandas is optional; files are written as gzipped CSV.

Examples
--------
# Hourly bars + funding for the 30 highest-turnover perps, from 2020:
python bybit_download.py --top 30 --interval 60 --start 2020-01-01

# 1-minute bars for a fixed list (for execution / cost modelling):
python bybit_download.py --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT --interval 1 --start 2021-01-01

# Raw trades for a date range (large: ~50-300 MB per symbol per day):
python bybit_download.py --symbols BTCUSDT --trades-from 2024-04-01 --trades-to 2024-04-07 --no-klines --no-funding

Re-running is safe: existing complete files are skipped unless --overwrite.
"""

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import sys
import time

import requests

API = "https://api.bybit.com"
TRADES_ARCHIVE = "https://public.bybit.com/trading"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "research-downloader/1.0"})

INTERVAL_MS = {
    "1": 60_000, "3": 180_000, "5": 300_000, "15": 900_000, "30": 1_800_000,
    "60": 3_600_000, "120": 7_200_000, "240": 14_400_000, "360": 21_600_000,
    "720": 43_200_000, "D": 86_400_000,
}


# --------------------------------------------------------------------------- #
# HTTP helper with retry / rate-limit handling
# --------------------------------------------------------------------------- #
def get(path, params, retries=8):
    url = API + path
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            if r.status_code in (403, 429) or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            data = r.json()
            # 10000 timeout, 10002 clock, 10006 rate limit, 10016 server/svc error:
            # all transient, so retry instead of failing.
            if data.get("retCode") in (10000, 10002, 10006, 10016):
                raise RuntimeError(f"transient Bybit error {data.get('retCode')}: {data.get('retMsg')}")
            if data.get("retCode") != 0:
                raise ValueError(f"Bybit error {data.get('retCode')}: {data.get('retMsg')} {params}")
            time.sleep(0.06)  # stay well under public limits
            return data["result"]
        except ValueError:
            raise
        except Exception as e:  # network, 5xx, rate limit
            wait = min(60, 2 ** attempt)
            print(f"  ! {e}; retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {path} {params}")


def to_ms(date_str):
    return int(dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def ms_to_iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- #
# Instruments / universe
# --------------------------------------------------------------------------- #
def fetch_instruments():
    out, cursor = [], None
    while True:
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        res = get("/v5/market/instruments-info", params)
        out.extend(res["list"])
        cursor = res.get("nextPageCursor")
        if not cursor:
            break
    return [i for i in out
            if i.get("contractType") == "LinearPerpetual" and i.get("quoteCoin") == "USDT"]


def top_by_turnover(n, instruments, listed_before_ms):
    # Only established contracts: trading now AND launched before the cutoff.
    # This drops new TradFi perps (gold, oil, stocks, ETF tokens) and recent listings.
    valid = {i["symbol"] for i in instruments
             if i.get("status") == "Trading"
             and 0 < int(i.get("launchTime") or 0) < listed_before_ms}
    tick = get("/v5/market/tickers", {"category": "linear"})["list"]
    tick = [t for t in tick if t["symbol"] in valid]
    tick.sort(key=lambda t: float(t.get("turnover24h") or 0), reverse=True)
    return [t["symbol"] for t in tick[:n]]


# --------------------------------------------------------------------------- #
# Klines (paginate backwards from now; Bybit returns newest first)
# --------------------------------------------------------------------------- #
def download_klines(symbol, interval, start_ms, path, category="linear"):
    step = INTERVAL_MS[interval]
    now_ms = int(time.time() * 1000)
    last_closed_open = (now_ms // step) * step - step  # drop the still-forming bar
    end = last_closed_open
    rows = {}
    while end >= start_ms:
        res = get("/v5/market/kline", {
            "category": category, "symbol": symbol, "interval": interval,
            "start": max(start_ms, end - 999 * step), "end": end, "limit": 1000,
        })
        batch = res["list"]
        if not batch:
            break  # nothing older (before listing)
        for k in batch:
            ts = int(k[0])
            if start_ms <= ts <= last_closed_open:
                rows[ts] = k
        oldest = int(batch[-1][0])
        if oldest >= end:  # safety: no progress
            break
        end = oldest - 1
        print(f"  {symbol} {interval}: back to {ms_to_iso(oldest)}  ({len(rows):,} bars)", end="\r")
    print()
    ts_sorted = sorted(rows)
    with gzip.open(path + ".part", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time_ms", "open_time_utc", "open", "high", "low", "close", "volume", "turnover"])
        for ts in ts_sorted:
            k = rows[ts]
            w.writerow([ts, ms_to_iso(ts), k[1], k[2], k[3], k[4], k[5], k[6]])
    os.replace(path + ".part", path)
    return audit_klines(ts_sorted, rows, step)


def audit_klines(ts_sorted, rows, step):
    """Report gaps and OHLC violations. Nothing is repaired or filled."""
    gaps, bad_ohlc = [], 0
    for a, b in zip(ts_sorted, ts_sorted[1:]):
        if b - a != step:
            gaps.append((ms_to_iso(a), ms_to_iso(b), (b - a) // step - 1))
    for ts in ts_sorted:
        o, h, l, c = (float(x) for x in rows[ts][1:5])
        if not (l <= min(o, c) and h >= max(o, c) and l <= h):
            bad_ohlc += 1
    return {
        "bars": len(ts_sorted),
        "first": ms_to_iso(ts_sorted[0]) if ts_sorted else None,
        "last": ms_to_iso(ts_sorted[-1]) if ts_sorted else None,
        "gap_count": len(gaps),
        "missing_bars": sum(g[2] for g in gaps),
        "largest_gaps": sorted(gaps, key=lambda g: -g[2])[:10],
        "bad_ohlc_rows": bad_ohlc,
    }


# --------------------------------------------------------------------------- #
# Funding history (paginate backwards with endTime)
# --------------------------------------------------------------------------- #
def download_funding(symbol, start_ms, path):
    end = int(time.time() * 1000)
    rows = {}
    while end >= start_ms:
        res = get("/v5/market/funding/history", {
            "category": "linear", "symbol": symbol, "endTime": end, "limit": 200,
        })
        batch = res["list"]
        if not batch:
            break
        for r in batch:
            ts = int(r["fundingRateTimestamp"])
            if ts >= start_ms:
                rows[ts] = r["fundingRate"]
        oldest = min(int(r["fundingRateTimestamp"]) for r in batch)
        if oldest >= end:
            break
        end = oldest - 1
    with gzip.open(path + ".part", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["funding_time_ms", "funding_time_utc", "funding_rate"])
        for ts in sorted(rows):
            w.writerow([ts, ms_to_iso(ts), rows[ts]])
    os.replace(path + ".part", path)
    return len(rows)


# --------------------------------------------------------------------------- #
# Raw trades from the public archive (one gz file per symbol per day)
# Columns include timestamp, side (aggressor), size, price, ...
# --------------------------------------------------------------------------- #
def download_trades(symbol, day_from, day_to, outdir, overwrite):
    d = dt.date.fromisoformat(day_from)
    last = dt.date.fromisoformat(day_to)
    os.makedirs(outdir, exist_ok=True)
    while d <= last:
        name = f"{symbol}{d.isoformat()}.csv.gz"
        dest = os.path.join(outdir, name)
        if os.path.exists(dest) and not overwrite:
            d += dt.timedelta(days=1)
            continue
        url = f"{TRADES_ARCHIVE}/{symbol}/{name}"
        for attempt in range(6):
            try:
                with SESSION.get(url, stream=True, timeout=60) as r:
                    if r.status_code == 404:
                        print(f"  {name}: not in archive")
                        break
                    r.raise_for_status()
                    with open(dest + ".part", "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                os.replace(dest + ".part", dest)
                print(f"  {name}: {os.path.getsize(dest)/1e6:.1f} MB")
                break
            except Exception as e:
                wait = min(60, 2 ** attempt)
                print(f"  ! {name}: {e}; retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
        d += dt.timedelta(days=1)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="*", default=[], help="e.g. BTCUSDT ETHUSDT")
    ap.add_argument("--top", type=int, default=0, help="add the N highest 24h-turnover USDT perps")
    ap.add_argument("--listed-before", default="2022-01-01",
                    help="with --top: only contracts launched before this date (default 2022-01-01)")
    ap.add_argument("--interval", default="60", choices=list(INTERVAL_MS), help="kline interval (default 60 = 1h)")
    ap.add_argument("--start", default="2020-01-01", help="UTC start date YYYY-MM-DD")
    ap.add_argument("--out", default="bybit_data", help="output folder")
    ap.add_argument("--no-klines", action="store_true")
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--trades-from", help="raw trades start date YYYY-MM-DD")
    ap.add_argument("--trades-to", help="raw trades end date YYYY-MM-DD")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    print("Fetching instrument list ...")
    instruments = fetch_instruments()
    by_symbol = {i["symbol"]: i for i in instruments}
    with open(os.path.join(args.out, "instruments_linear_usdt.json"), "w") as f:
        json.dump(instruments, f, indent=1)

    symbols = list(dict.fromkeys(s.upper() for s in args.symbols))
    if args.top:
        for s in top_by_turnover(args.top, instruments, to_ms(args.listed_before)):
            if s not in symbols:
                symbols.append(s)
    if not symbols:
        ap.error("give --symbols and/or --top")
    unknown = [s for s in symbols if s not in by_symbol]
    if unknown:
        print(f"Warning: not current USDT perps on Bybit, skipped: {unknown}")
        symbols = [s for s in symbols if s in by_symbol]
    print(f"Universe ({len(symbols)}): {' '.join(symbols)}")

    start_ms = to_ms(args.start)
    audit_path = os.path.join(args.out, f"audit_{args.interval}.json")
    audit = {}
    if os.path.exists(audit_path):
        with open(audit_path) as f:
            audit = json.load(f)

    failed = []
    for sym in symbols:
        try:
            process_symbol(sym, by_symbol, start_ms, args, audit, audit_path)
        except Exception as e:
            print(f"\n{sym}: FAILED ({e}). Moving on; re-run the same command later to retry.")
            failed.append(sym)

    if failed:
        print(f"\nFailed symbols (re-run to retry, finished ones are skipped): {' '.join(failed)}")
    print("\nDone. Note: the universe is today's listed contracts only, so delisted coins")
    print("are missing (survivorship bias). Gaps are reported in the audit file, never filled.")


def process_symbol(sym, by_symbol, start_ms, args, audit, audit_path):
    launch = int(by_symbol[sym].get("launchTime") or 0)
    sym_start = max(start_ms, launch)
    if not args.no_klines:
        path = os.path.join(args.out, f"{sym}_{args.interval}.csv.gz")
        if os.path.exists(path) and not args.overwrite:
            print(f"{sym}: klines exist, skipping")
        else:
            print(f"{sym}: klines from {ms_to_iso(sym_start)}")
            audit[sym] = download_klines(sym, args.interval, sym_start, path)
            a = audit[sym]
            print(f"  {a['bars']:,} bars {a['first']} -> {a['last']}; "
                  f"gaps={a['gap_count']} missing={a['missing_bars']} bad_ohlc={a['bad_ohlc_rows']}")
            with open(audit_path, "w") as f:
                json.dump(audit, f, indent=1)
    if not args.no_funding:
        path = os.path.join(args.out, f"{sym}_funding.csv.gz")
        if os.path.exists(path) and not args.overwrite:
            print(f"{sym}: funding exists, skipping")
        else:
            n = download_funding(sym, sym_start, path)
            print(f"{sym}: {n:,} funding records")
    if args.trades_from and args.trades_to:
        download_trades(sym, args.trades_from, args.trades_to,
                        os.path.join(args.out, "trades", sym), args.overwrite)


if __name__ == "__main__":
    main()
