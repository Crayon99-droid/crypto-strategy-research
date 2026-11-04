#!/usr/bin/env python3
"""
Round-2 data for the carry and volatility-premium tests.

Put this file in the same folder as bybit_download.py and run it from the folder
that contains bybit_data:

    py download_round2.py

It adds to bybit_data/:
  * <PERP>_spot_60.csv.gz : Bybit SPOT hourly candles for each perp already
                            downloaded (SHIB1000USDT maps to spot SHIBUSDT).
                            Coins with no Bybit spot pair are listed and skipped.
  * spot_audit_60.json    : gap / OHLC report for the spot files
  * DVOL_BTC_60.csv.gz, DVOL_ETH_60.csv.gz : Deribit 30-day implied-volatility
                            index, hourly OHLC, from its start (2021).
Re-running skips finished files.
"""
import csv, glob, gzip, json, os, sys, time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bybit_download as B

OUT = "bybit_data"
SPOT_ALIAS = {"SHIB1000USDT": "SHIBUSDT"}   # perp name -> spot name
START = "2020-01-01"


def spot_symbols():
    out, cursor = set(), None
    while True:
        params = {"category": "spot", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        res = B.get("/v5/market/instruments-info", params)
        out |= {i["symbol"] for i in res["list"]}
        cursor = res.get("nextPageCursor")
        if not cursor:
            return out


def deribit_dvol(currency, path):
    url = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
    start = B.to_ms("2021-01-01")
    end = int(time.time() * 1000)
    rows = {}
    while end > start:
        for attempt in range(8):
            try:
                r = requests.get(url, params={"currency": currency, "start_timestamp": start,
                                              "end_timestamp": end, "resolution": "3600"}, timeout=30)
                r.raise_for_status()
                res = r.json()["result"]
                break
            except Exception as e:
                wait = min(60, 2 ** attempt)
                print(f"  ! Deribit {e}; retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
        else:
            raise RuntimeError("Deribit unreachable")
        data = res.get("data") or []
        for ts, o, h, l, c in data:
            rows[int(ts)] = (o, h, l, c)
        cont = res.get("continuation")
        if not data or not cont or int(cont) >= end:
            break
        end = int(cont)
        print(f"  DVOL {currency}: back to {B.ms_to_iso(min(rows))} ({len(rows):,} hours)", end="\r")
        time.sleep(0.2)
    print()
    with gzip.open(path + ".part", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_ms", "time_utc", "open", "high", "low", "close"])
        for ts in sorted(rows):
            w.writerow([ts, B.ms_to_iso(ts), *rows[ts]])
    os.replace(path + ".part", path)
    return len(rows)


def main():
    if not os.path.isdir(OUT):
        sys.exit("Run this from the folder that contains bybit_data")
    perps = sorted(os.path.basename(p).split("_")[0] for p in glob.glob(f"{OUT}/*USDT_60.csv.gz"))
    perps = [p for p in perps if not p.endswith("_spot")]
    listed = spot_symbols()
    audit_path = f"{OUT}/spot_audit_60.json"
    audit = json.load(open(audit_path)) if os.path.exists(audit_path) else {}
    missing = []
    for p in perps:
        s = SPOT_ALIAS.get(p, p)
        if s not in listed:
            missing.append(p)
            continue
        path = f"{OUT}/{p}_spot_60.csv.gz"
        if os.path.exists(path):
            print(f"{p}: spot exists, skipping")
            continue
        try:
            print(f"{p}: spot {s}")
            audit[p] = B.download_klines(s, "60", B.to_ms(START), path, category="spot")
            a = audit[p]
            print(f"  {a['bars']:,} bars {a['first']} -> {a['last']}; gaps={a['gap_count']} missing={a['missing_bars']}")
            json.dump(audit, open(audit_path, "w"), indent=1)
        except Exception as e:
            print(f"{p}: FAILED ({e}); re-run later")
    print(f"\nNo Bybit spot pair (skipped): {' '.join(missing) or 'none'}")

    for cur in ("BTC", "ETH"):
        path = f"{OUT}/DVOL_{cur}_60.csv.gz"
        if os.path.exists(path):
            print(f"DVOL {cur}: exists, skipping")
            continue
        n = deribit_dvol(cur, path)
        print(f"DVOL {cur}: {n:,} hours")
    print("\nDone. Zip the bybit_data folder again and attach it.")


if __name__ == "__main__":
    main()
