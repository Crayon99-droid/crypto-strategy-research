#!/usr/bin/env python3
"""Download Bybit USDT-perpetual research data without third-party packages.

Outputs
-------
* One ZIP per symbol/year containing UTC one-minute OHLCV candles.
* One funding-history CSV per symbol.
* One instrument-specification JSON per symbol.
* download_manifest.json with row counts, gaps, hashes, and source details.

The end date is exclusive. For example, --end 2026-08-23 downloads through
2026-08-22 23:59:00 UTC when that date is already in the past. If the requested
end is in the future, the downloader automatically stops before the currently
forming one-minute candle.

Official endpoints used:
  https://api.bybit.com/v5/market/kline
  https://api.bybit.com/v5/market/funding/history
  https://api.bybit.com/v5/market/instruments-info
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc
MINUTE_MS = 60_000
KLINE_LIMIT = 1_000
FUNDING_LIMIT = 200
USER_AGENT = "Helios-Research-Downloader/1.0"


def parse_utc_date(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}; use YYYY-MM-DD."
        ) from exc
    return parsed.replace(tzinfo=UTC)


def to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1_000)


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000, tz=UTC).isoformat().replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BybitPublicClient:
    def __init__(self, base_url: str, pause: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.pause = max(0.0, pause)

    def get_json(
        self, path: str, params: dict[str, Any], retries: int = 8
    ) -> dict[str, Any]:
        encoded = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{encoded}"
        last_error: Exception | None = None

        for attempt in range(retries):
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                if payload.get("retCode") != 0:
                    raise RuntimeError(
                        f"Bybit error {payload.get('retCode')}: "
                        f"{payload.get('retMsg')}"
                    )

                if self.pause:
                    time.sleep(self.pause)
                return payload

            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                json.JSONDecodeError,
                RuntimeError,
            ) as exc:
                last_error = exc
                if attempt == retries - 1:
                    break
                delay = min(60.0, (2**attempt) + random.random())
                print(
                    f"  Request failed ({exc}); retrying in {delay:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)

        raise RuntimeError(f"Request failed after {retries} attempts: {url}") from last_error


def get_instrument(client: BybitPublicClient, symbol: str) -> dict[str, Any]:
    payload = client.get_json(
        "/v5/market/instruments-info",
        {"category": "linear", "symbol": symbol},
    )
    instruments = payload.get("result", {}).get("list", [])
    if not instruments:
        raise RuntimeError(f"No linear instrument information returned for {symbol}.")
    return instruments[0]


def download_kline_year(
    client: BybitPublicClient,
    symbol: str,
    start_ms: int,
    end_ms: int,
    output_dir: Path,
    overwrite: bool,
) -> dict[str, Any]:
    year = datetime.fromtimestamp(start_ms / 1_000, tz=UTC).year
    zip_path = output_dir / f"{symbol}_1m_{year}.csv.zip"
    partial_path = zip_path.with_suffix(zip_path.suffix + ".partial")
    csv_name = f"{symbol}_1m_{year}.csv"

    if zip_path.exists() and not overwrite:
        print(f"  Skipping existing {zip_path.name}")
        return {
            "file": zip_path.name,
            "status": "existing",
            "sha256": sha256_file(zip_path),
        }

    if partial_path.exists():
        partial_path.unlink()

    cursor = start_ms
    rows_written = 0
    duplicate_rows = 0
    missing_minutes = 0
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    last_reported = time.monotonic()

    with zipfile.ZipFile(
        partial_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        with archive.open(csv_name, mode="w") as binary_handle:
            with io.TextIOWrapper(
                binary_handle, encoding="utf-8", newline=""
            ) as text_handle:
                writer = csv.writer(text_handle)
                writer.writerow(
                    [
                        "timestamp_utc",
                        "timestamp_ms",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume_base",
                        "turnover_usdt",
                    ]
                )

                while cursor < end_ms:
                    window_end = min(
                        end_ms - 1,
                        cursor + (KLINE_LIMIT - 1) * MINUTE_MS,
                    )
                    payload = client.get_json(
                        "/v5/market/kline",
                        {
                            "category": "linear",
                            "symbol": symbol,
                            "interval": "1",
                            "start": cursor,
                            "end": window_end,
                            "limit": KLINE_LIMIT,
                        },
                    )
                    raw_rows = payload.get("result", {}).get("list", [])

                    if not raw_rows:
                        cursor = window_end + MINUTE_MS
                        continue

                    parsed_rows = sorted(raw_rows, key=lambda row: int(row[0]))
                    newest_timestamp = cursor - MINUTE_MS

                    for row in parsed_rows:
                        timestamp = int(row[0])
                        if timestamp < start_ms or timestamp >= end_ms:
                            continue
                        newest_timestamp = max(newest_timestamp, timestamp)

                        if last_timestamp is not None and timestamp <= last_timestamp:
                            duplicate_rows += 1
                            continue

                        if last_timestamp is not None:
                            difference = timestamp - last_timestamp
                            if difference > MINUTE_MS:
                                missing_minutes += difference // MINUTE_MS - 1

                        writer.writerow(
                            [
                                iso_from_ms(timestamp),
                                timestamp,
                                row[1],
                                row[2],
                                row[3],
                                row[4],
                                row[5],
                                row[6],
                            ]
                        )
                        first_timestamp = (
                            timestamp if first_timestamp is None else first_timestamp
                        )
                        last_timestamp = timestamp
                        rows_written += 1

                    if newest_timestamp < cursor:
                        cursor = window_end + MINUTE_MS
                    else:
                        cursor = newest_timestamp + MINUTE_MS

                    now = time.monotonic()
                    if now - last_reported >= 10:
                        progress = 100.0 * (cursor - start_ms) / (end_ms - start_ms)
                        print(
                            f"  {symbol} {year}: {progress:5.1f}% "
                            f"({rows_written:,} rows)"
                        )
                        last_reported = now

    partial_path.replace(zip_path)
    print(
        f"  Wrote {zip_path.name}: {rows_written:,} rows, "
        f"{missing_minutes:,} missing minute(s)"
    )

    return {
        "file": zip_path.name,
        "status": "downloaded",
        "rows": rows_written,
        "duplicates_skipped": duplicate_rows,
        "missing_minutes": missing_minutes,
        "first_timestamp_utc": (
            iso_from_ms(first_timestamp) if first_timestamp is not None else None
        ),
        "last_timestamp_utc": (
            iso_from_ms(last_timestamp) if last_timestamp is not None else None
        ),
        "sha256": sha256_file(zip_path),
    }


def download_funding(
    client: BybitPublicClient,
    symbol: str,
    start_ms: int,
    end_ms: int,
    output_dir: Path,
    overwrite: bool,
) -> dict[str, Any]:
    path = output_dir / f"{symbol}_funding.csv"
    if path.exists() and not overwrite:
        print(f"  Skipping existing {path.name}")
        return {
            "file": path.name,
            "status": "existing",
            "sha256": sha256_file(path),
        }

    cursor_end = end_ms - 1
    records: dict[int, str] = {}

    while cursor_end >= start_ms:
        payload = client.get_json(
            "/v5/market/funding/history",
            {
                "category": "linear",
                "symbol": symbol,
                "endTime": cursor_end,
                "limit": FUNDING_LIMIT,
            },
        )
        raw_records = payload.get("result", {}).get("list", [])
        if not raw_records:
            break

        timestamps = []
        for record in raw_records:
            timestamp = int(record["fundingRateTimestamp"])
            timestamps.append(timestamp)
            if start_ms <= timestamp < end_ms:
                records[timestamp] = record["fundingRate"]

        oldest = min(timestamps)
        if oldest <= start_ms:
            break
        cursor_end = oldest - 1

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp_utc", "timestamp_ms", "funding_rate"])
        for timestamp in sorted(records):
            writer.writerow([iso_from_ms(timestamp), timestamp, records[timestamp]])

    print(f"  Wrote {path.name}: {len(records):,} funding records")
    return {
        "file": path.name,
        "status": "downloaded",
        "rows": len(records),
        "sha256": sha256_file(path),
    }


def year_ranges(start: datetime, end: datetime):
    current_year = start.year
    while current_year <= end.year:
        year_start = datetime(current_year, 1, 1, tzinfo=UTC)
        next_year = datetime(current_year + 1, 1, 1, tzinfo=UTC)
        range_start = max(start, year_start)
        range_end = min(end, next_year)
        if range_start < range_end:
            yield range_start, range_end
        current_year += 1


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download official Bybit 1-minute and funding data."
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["ETHUSDT", "XRPUSDT"],
        help="Uppercase linear-perpetual symbols (default: ETHUSDT XRPUSDT).",
    )
    parser.add_argument(
        "--start",
        type=parse_utc_date,
        default=parse_utc_date("2021-01-01"),
        help="Inclusive UTC start date (default: 2021-01-01).",
    )
    parser.add_argument(
        "--end",
        type=parse_utc_date,
        default=parse_utc_date("2026-08-23"),
        help="Exclusive UTC end date (default: 2026-08-23).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("bybit_data"),
        help="Output directory (default: bybit_data).",
    )
    parser.add_argument(
        "--base-url",
        default="https://api.bybit.com",
        help="Bybit API base URL (default: https://api.bybit.com).",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.08,
        help="Pause after successful requests in seconds (default: 0.08).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace files that already exist.",
    )
    parser.add_argument(
        "--skip-funding",
        action="store_true",
        help="Download candles and instrument rules but not funding history.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    if args.start >= args.end:
        print("Error: --start must be earlier than --end.", file=sys.stderr)
        return 2

    # A kline stamped 19:08 is still forming until 19:09. Use the current
    # minute boundary as an exclusive upper bound, so only closed bars enter
    # research files even when the requested end date has not arrived yet.
    created_at = datetime.now(tz=UTC)
    last_closed_bar_end = created_at.replace(second=0, microsecond=0)
    available_end = min(args.end, last_closed_bar_end)
    if args.start >= available_end:
        print(
            "Error: the requested range contains no fully closed one-minute bars.",
            file=sys.stderr,
        )
        return 2

    symbols = [symbol.upper().strip() for symbol in args.symbols]
    if any(not symbol.endswith("USDT") for symbol in symbols):
        print(
            "Error: this downloader is configured for USDT linear contracts.",
            file=sys.stderr,
        )
        return 2

    output_dir: Path = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    client = BybitPublicClient(args.base_url, args.pause)

    manifest: dict[str, Any] = {
        "created_utc": created_at.isoformat().replace("+00:00", "Z"),
        "requested_start_utc": args.start.isoformat().replace("+00:00", "Z"),
        "requested_end_exclusive_utc": args.end.isoformat().replace(
            "+00:00", "Z"
        ),
        "effective_end_exclusive_utc": available_end.isoformat().replace(
            "+00:00", "Z"
        ),
        "forming_minute_excluded": args.end > last_closed_bar_end,
        "api_base_url": args.base_url,
        "sources": {
            "klines": "/v5/market/kline",
            "funding": "/v5/market/funding/history",
            "instruments": "/v5/market/instruments-info",
        },
        "symbols": {},
    }

    for symbol in symbols:
        print(f"\n{symbol}")
        instrument = get_instrument(client, symbol)
        instrument_path = output_dir / f"{symbol}_instrument.json"
        with instrument_path.open("w", encoding="utf-8") as handle:
            json.dump(instrument, handle, indent=2, sort_keys=True)
            handle.write("\n")

        launch_ms = int(instrument.get("launchTime", "0") or "0")
        effective_start = max(args.start, datetime.fromtimestamp(launch_ms / 1_000, tz=UTC))
        effective_end = available_end
        if effective_start >= effective_end:
            print(f"  No data range available for {symbol}.")
            continue

        symbol_manifest: dict[str, Any] = {
            "instrument_file": instrument_path.name,
            "instrument_sha256": sha256_file(instrument_path),
            "launch_time_utc": iso_from_ms(launch_ms) if launch_ms else None,
            "effective_start_utc": effective_start.isoformat().replace(
                "+00:00", "Z"
            ),
            "candle_files": [],
        }

        for range_start, range_end in year_ranges(effective_start, effective_end):
            print(f"  Downloading {range_start.year} one-minute candles...")
            result = download_kline_year(
                client,
                symbol,
                to_ms(range_start),
                to_ms(range_end),
                output_dir,
                args.overwrite,
            )
            symbol_manifest["candle_files"].append(result)

        if not args.skip_funding:
            print("  Downloading funding history...")
            symbol_manifest["funding_file"] = download_funding(
                client,
                symbol,
                to_ms(effective_start),
                to_ms(effective_end),
                output_dir,
                args.overwrite,
            )

        manifest["symbols"][symbol] = symbol_manifest

        manifest_path = output_dir / "download_manifest.json"
        with manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")

    print(f"\nFinished. Files are in: {output_dir}")
    print("Upload the yearly ZIPs, funding CSVs, instrument JSONs, and manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
