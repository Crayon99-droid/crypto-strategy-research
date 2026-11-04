#!/usr/bin/env python3
"""Final preregistered RR 1.5 directional-change research engine.

The engine is deliberately causal and venue-specific.  It runs every
volatility multiplier as a separate directional-change stream, freezes open
trade exits at entry, and uses expanding walk-forward threshold maps.  A
regime is mapped to no trade unless a multiplier survives the preregistered
coverage and family-wise statistical gate.  Relative to Phase 4, reward/risk
is the only research parameter allowed to change.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3
import statistics
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ENGINE_VERSION = "phase5-final-rr15-v1"
MINUTE_MS = 60_000
DAY_MS = 86_400_000
WEEK_MS = 604_800_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, allow_nan=False, default=json_default) + "\n",
        encoding="utf-8",
    )


def json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iso_ms(value: str) -> int:
    return int(pd.Timestamp(value).timestamp() * 1000)


def halfyear(timestamp_ms: int) -> str:
    value = pd.Timestamp(timestamp_ms, unit="ms", tz="UTC")
    return f"{value.year} H{1 if value.month <= 6 else 2}"


def tick_round(value: float, tick: float, direction: int) -> float:
    units = value / tick
    if direction > 0:
        return math.ceil(units - 1e-10) * tick
    return math.floor(units + 1e-10) * tick


def profit_factor(values: np.ndarray) -> float | None:
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    return float(gains / losses) if losses > 0 else None


def clean_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        output: dict[str, Any] = {}
        for key, value in row.items():
            if pd.isna(value):
                output[key] = None
            elif isinstance(value, np.generic):
                output[key] = value.item()
            else:
                output[key] = value
        records.append(output)
    return records


def audit_sources(
    data_dir: Path, reference_audit: Path, results_dir: Path
) -> tuple[pd.DataFrame, pd.DataFrame]:
    reference = load_json(reference_audit)
    expected = {
        item["file"]: item
        for section in ("candle_files", "funding_files", "instrument_files")
        for item in reference[section]
    }
    candle_names = [
        f"{symbol}_1m_{year}.csv.zip"
        for symbol in ("ETHUSDT", "XRPUSDT")
        for year in range(2021, 2025)
    ]
    names = candle_names + [
        "ETHUSDT_funding(1).csv",
        "XRPUSDT_funding(1).csv",
        "ETHUSDT_instrument(1).json",
        "XRPUSDT_instrument(1).json",
    ]
    source_rows: list[dict[str, Any]] = []
    candle_rows: list[dict[str, Any]] = []
    for name in names:
        path = data_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        item = expected[name]
        actual_hash = sha256(path)
        hash_ok = actual_hash == item["expected_sha256"]
        row: dict[str, Any] = {
            "file": name,
            "size_bytes": path.stat().st_size,
            "sha256": actual_hash,
            "expected_sha256": item["expected_sha256"],
            "hash_ok": hash_ok,
            "kind": "candle" if name.endswith(".zip") else "funding" if "funding" in name else "instrument",
            "schema_ok": True,
            "chronology_errors": 0,
            "duplicate_timestamps": 0,
            "numeric_ok": True,
            "coverage_ok": True,
            "zip_crc_ok": None,
        }
        if name.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
                members = archive.namelist()
            frame = pd.read_csv(path, compression="zip")
            required = [
                "timestamp_utc", "timestamp_ms", "open", "high", "low", "close",
                "volume_base", "turnover_usdt",
            ]
            schema_ok = frame.columns.tolist() == required
            numeric = frame[["open", "high", "low", "close", "volume_base", "turnover_usdt"]].apply(
                pd.to_numeric, errors="coerce"
            )
            timestamps = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
            chronology = int((timestamps.diff().iloc[1:] != MINUTE_MS).sum())
            duplicates = int(timestamps.duplicated().sum())
            numeric_ok = bool(np.isfinite(numeric.to_numpy(float)).all())
            ohlc_ok = bool(
                (
                    (numeric["high"] >= numeric[["open", "close"]].max(axis=1))
                    & (numeric["low"] <= numeric[["open", "close"]].min(axis=1))
                    & (numeric["high"] >= numeric["low"])
                ).all()
            )
            first = pd.to_datetime(int(timestamps.iloc[0]), unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ")
            last = pd.to_datetime(int(timestamps.iloc[-1]), unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ")
            coverage_ok = first == item["first_timestamp_utc"] and last == item["last_timestamp_utc"]
            row.update(
                {
                    "schema_ok": schema_ok,
                    "chronology_errors": chronology,
                    "duplicate_timestamps": duplicates,
                    "numeric_ok": numeric_ok,
                    "coverage_ok": coverage_ok,
                    "zip_crc_ok": bad is None and len(members) == 1,
                }
            )
            passed = bool(
                hash_ok and schema_ok and chronology == 0 and duplicates == 0
                and numeric_ok and ohlc_ok and coverage_ok and bad is None
                and len(members) == 1 and len(frame) == int(item["expected_rows"])
            )
            candle_rows.append(
                {
                    "file": name,
                    "rows": len(frame),
                    "expected_rows": int(item["expected_rows"]),
                    "first_timestamp_utc": first,
                    "last_timestamp_utc": last,
                    "chronology_errors": chronology,
                    "duplicate_timestamps": duplicates,
                    "numeric_ok": numeric_ok,
                    "ohlc_ok": ohlc_ok,
                    "coverage_ok": coverage_ok,
                    "archive_crc_ok": bad is None,
                    "passed": passed,
                }
            )
        elif "funding" in name:
            frame = pd.read_csv(path)
            required = ["timestamp_utc", "timestamp_ms", "funding_rate"]
            timestamps = pd.to_numeric(frame.get("timestamp_ms"), errors="coerce")
            rates = pd.to_numeric(frame.get("funding_rate"), errors="coerce")
            row.update(
                {
                    "schema_ok": frame.columns.tolist() == required,
                    "chronology_errors": int((timestamps.diff().iloc[1:] <= 0).sum()),
                    "duplicate_timestamps": int(timestamps.duplicated().sum()),
                    "numeric_ok": bool(np.isfinite(rates.to_numpy(float)).all()),
                    "coverage_ok": len(frame) == int(item["expected_rows"]),
                }
            )
        else:
            instrument = load_json(path)
            symbol = name.split("_")[0]
            row.update(
                {
                    "schema_ok": bool(
                        instrument.get("symbol") == symbol
                        and float(instrument["priceFilter"]["tickSize"]) > 0
                        and float(instrument["lotSizeFilter"]["qtyStep"]) > 0
                    ),
                    "coverage_ok": True,
                }
            )
        row["passed"] = bool(
            row["hash_ok"] and row["schema_ok"] and row["chronology_errors"] == 0
            and row["duplicate_timestamps"] == 0 and row["numeric_ok"]
            and row["coverage_ok"] and row.get("zip_crc_ok") is not False
        )
        source_rows.append(row)
    sources = pd.DataFrame(source_rows)
    candles = pd.DataFrame(candle_rows)
    results_dir.mkdir(parents=True, exist_ok=True)
    sources.to_csv(results_dir / "phase5_source_hash_audit.csv", index=False)
    candles.to_csv(results_dir / "phase5_candle_integrity_audit.csv", index=False)
    if not bool(sources["passed"].all()) or not bool(candles["passed"].all()):
        raise RuntimeError("Source-data audit failed")
    return sources, candles


def load_market(data_dir: Path, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    bars = pd.concat(
        [
            pd.read_csv(data_dir / f"{symbol}_1m_{year}.csv.zip", compression="zip")
            for year in range(2021, 2025)
        ],
        ignore_index=True,
    )
    funding = pd.read_csv(data_dir / f"{symbol}_funding(1).csv")
    instrument = load_json(data_dir / f"{symbol}_instrument(1).json")
    return bars, funding, instrument


@dataclass
class Detector:
    multiplier: float
    direction: int = 0
    high: float = math.nan
    low: float = math.nan
    high_index: int = -1
    low_index: int = -1
    sequence: int = 0


def daily_and_intraday_features(
    bars: pd.DataFrame, config: dict[str, Any]
) -> dict[str, np.ndarray]:
    close = bars["close"].astype(float)
    timestamps = bars["timestamp_ms"].to_numpy(np.int64)
    log_return = np.log(close).diff()
    rv240 = (
        log_return.rolling(240, min_periods=240).std(ddof=1)
        * math.sqrt(240) * 10_000.0
    )
    return240 = (close / close.shift(240) - 1.0) * 10_000.0
    days = pd.to_datetime(timestamps, unit="ms", utc=True).floor("D")
    daily = pd.DataFrame({"day": days, "rv240_bp": rv240}).groupby("day", sort=True)["rv240_bp"].median()
    previous = daily.shift(1)
    vol_cfg = config["volatility"]
    trailing = previous.rolling(
        int(vol_cfg["regime_lookback_days"]),
        min_periods=int(vol_cfg["regime_min_history_days"]),
    )
    qlow, qhigh = vol_cfg["regime_quantiles"]
    daily_table = pd.DataFrame(
        {
            "sigma_bp": previous,
            "low_cut_bp": trailing.quantile(float(qlow)),
            "high_cut_bp": trailing.quantile(float(qhigh)),
        }
    )
    day_series = pd.Series(days)
    sigma = day_series.map(daily_table["sigma_bp"]).to_numpy(float)
    low_cut = day_series.map(daily_table["low_cut_bp"]).to_numpy(float)
    high_cut = day_series.map(daily_table["high_cut_bp"]).to_numpy(float)
    vol_level = np.full(len(bars), -1, np.int8)
    ready = np.isfinite(sigma) & np.isfinite(low_cut) & np.isfinite(high_cut)
    vol_level[ready] = 1
    vol_level[ready & (sigma < low_cut)] = 0
    vol_level[ready & (sigma > high_cut)] = 2

    score = np.abs(return240.to_numpy(float) / rv240.to_numpy(float))
    structure = np.zeros(len(bars), np.int8)  # 0 range, 1 trend
    state = 0
    enter = float(config["structure"]["trend_enter"])
    leave = float(config["structure"]["trend_exit"])
    for index, value in enumerate(score):
        if math.isfinite(value):
            if state == 0 and value >= enter:
                state = 1
            elif state == 1 and value <= leave:
                state = 0
        structure[index] = state
    return {
        "rv240_bp": rv240.to_numpy(float),
        "return240_bp": return240.to_numpy(float),
        "trend_score": score,
        "sigma_bp": sigma,
        "vol_level": vol_level,
        "structure": structure,
    }


def funding_component_bp(
    side: int,
    entry_ms: int,
    exit_ms: int,
    funding_times: np.ndarray,
    funding_prefix: np.ndarray,
) -> float:
    left = int(np.searchsorted(funding_times, entry_ms, side="right"))
    right = int(np.searchsorted(funding_times, exit_ms, side="right"))
    paid = float(funding_prefix[right] - funding_prefix[left])
    return -side * paid * 10_000.0


def evaluate_trade(
    side: int,
    signal_index: int,
    extreme: float,
    market: dict[str, np.ndarray | float],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    entry_index = signal_index + 1
    maximum = int(config["trade"]["maximum_holding_minutes"])
    # Keep one completed bar beyond the frozen 240-minute label window.  This
    # makes the end-of-development boundary explicit and prevents the final
    # bar of a source archive from serving as both label and dataset boundary.
    if entry_index + maximum >= len(market["timestamps"]):
        return None
    tick = float(market["tick"])
    entry_open = float(market["opens"][entry_index])
    entry_slip = float(config["execution"]["entry_slippage_bp"])
    entry = tick_round(entry_open * (1.0 + side * entry_slip / 10_000.0), tick, side)
    stop = tick_round(extreme, tick, -side)
    risk = (entry - stop) if side == 1 else (stop - entry)
    if not math.isfinite(risk) or risk <= 0:
        return None
    target_raw = entry + side * float(config["trade"]["reward_risk"]) * risk
    target = tick_round(target_raw, tick, side)
    end = entry_index + maximum
    highs = market["highs"][entry_index:end]
    lows = market["lows"][entry_index:end]
    if side == 1:
        tp_hits = np.flatnonzero(highs >= target)
        sl_hits = np.flatnonzero(lows <= stop)
    else:
        tp_hits = np.flatnonzero(lows <= target)
        sl_hits = np.flatnonzero(highs >= stop)
    tp_minute = int(tp_hits[0]) + 1 if tp_hits.size else 0
    sl_minute = int(sl_hits[0]) + 1 if sl_hits.size else 0
    ambiguous = bool(tp_minute and sl_minute and tp_minute == sl_minute)
    if sl_minute and (not tp_minute or sl_minute <= tp_minute):
        outcome = "sl_first"
        exit_minute = sl_minute
        exit_reference = stop
    elif tp_minute:
        outcome = "tp_first"
        exit_minute = tp_minute
        exit_reference = target
    else:
        outcome = "timeout"
        exit_minute = maximum
        exit_reference = float(market["closes"][end - 1])
    exit_slip = float(config["execution"]["exit_slippage_bp"])
    exit_raw = exit_reference * (1.0 - side * exit_slip / 10_000.0)
    exit_fill = tick_round(exit_raw, tick, -side)
    gross_reference = side * (exit_reference / entry - 1.0) * 10_000.0
    gross_after_slippage = side * (exit_fill / entry - 1.0) * 10_000.0
    fee_rate = float(config["execution"]["taker_fee_bp_per_fill"])
    # Fees are charged on both fill notionals.  Expressing exit commission in
    # entry-notional basis avoids the false constant-11-bp approximation.
    fee = fee_rate + fee_rate * exit_fill / entry
    exit_ms = int(market["timestamps"][entry_index + exit_minute - 1])
    funding = funding_component_bp(
        side,
        int(market["timestamps"][entry_index]),
        exit_ms,
        market["funding_times"],
        market["funding_prefix"],
    )
    signal_path_gross = side * (exit_reference / entry_open - 1.0) * 10_000.0
    slippage_and_tick_cost = signal_path_gross - gross_after_slippage
    execution_cost = slippage_and_tick_cost + fee
    return {
        "entry_index": entry_index,
        "entry_timestamp_ms": int(market["timestamps"][entry_index]),
        "exit_timestamp_ms": exit_ms,
        "busy_until_ms": int(market["timestamps"][entry_index]) + exit_minute * MINUTE_MS,
        "exit_minutes": exit_minute,
        "entry_open": entry_open,
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "exit_reference_price": exit_reference,
        "exit_fill_price": exit_fill,
        "risk_bp": risk / entry * 10_000.0,
        "outcome": outcome,
        "ambiguous_same_minute": int(ambiguous),
        "gross_reference_bp": gross_reference,
        "gross_after_slippage_bp": gross_after_slippage,
        "fee_bp": fee,
        "funding_bp": funding,
        "net_return_bp": gross_after_slippage - fee + funding,
        "signal_path_gross_bp": signal_path_gross,
        "slippage_and_tick_cost_bp": slippage_and_tick_cost,
        "execution_cost_bp": execution_cost,
        "half_execution_cost_net_bp": signal_path_gross - 0.5 * execution_cost + funding,
        "zero_execution_cost_net_bp": signal_path_gross + funding,
    }


def collect_symbol(
    data_dir: Path, symbol: str, config: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    bars, funding, instrument = load_market(data_dir, symbol)
    features = daily_and_intraday_features(bars, config)
    timestamps = bars["timestamp_ms"].to_numpy(np.int64)
    market: dict[str, Any] = {
        "timestamps": timestamps,
        "opens": bars["open"].to_numpy(float),
        "highs": bars["high"].to_numpy(float),
        "lows": bars["low"].to_numpy(float),
        "closes": bars["close"].to_numpy(float),
        "tick": float(instrument["priceFilter"]["tickSize"]),
        "funding_times": funding["timestamp_ms"].to_numpy(np.int64),
    }
    rates = funding["funding_rate"].to_numpy(float)
    market["funding_prefix"] = np.concatenate(([0.0], np.cumsum(rates)))
    detectors = [Detector(float(value)) for value in config["threshold_multipliers"]]
    names_vol = np.asarray(["low", "normal", "high"], dtype=object)
    names_structure = np.asarray(["range", "trend"], dtype=object)
    rows: list[dict[str, Any]] = []
    raw_events = defaultdict(int)
    excluded_regime = 0
    excluded_horizon = 0
    excluded_geometry = 0
    for index in range(len(bars) - 1):
        current_high = float(market["highs"][index])
        current_low = float(market["lows"][index])
        current_close = float(market["closes"][index])
        sigma_bp = float(features["sigma_bp"][index])
        if not math.isfinite(sigma_bp) or sigma_bp <= 0:
            continue
        for detector in detectors:
            theta = detector.multiplier * sigma_bp / 10_000.0
            if detector.high_index < 0:
                detector.high = current_high
                detector.low = current_low
                detector.high_index = index
                detector.low_index = index
                continue
            signal_side = 0
            extreme = math.nan
            extreme_index = -1
            if detector.direction == 0:
                if current_high > detector.high:
                    detector.high = current_high
                    detector.high_index = index
                if current_low < detector.low:
                    detector.low = current_low
                    detector.low_index = index
                up = current_close / detector.low - 1.0
                down = 1.0 - current_close / detector.high
                if up >= theta and up >= down:
                    detector.direction = 1
                    detector.high = current_close
                    detector.high_index = index
                elif down >= theta:
                    detector.direction = -1
                    detector.low = current_close
                    detector.low_index = index
                continue
            if detector.direction == 1:
                if current_high > detector.high:
                    detector.high = current_high
                    detector.high_index = index
                if 1.0 - current_close / detector.high >= theta:
                    signal_side = -1
                    extreme = detector.high
                    extreme_index = detector.high_index
                    detector.direction = -1
                    detector.low = current_close
                    detector.low_index = index
            else:
                if current_low < detector.low:
                    detector.low = current_low
                    detector.low_index = index
                if current_close / detector.low - 1.0 >= theta:
                    signal_side = 1
                    extreme = detector.low
                    extreme_index = detector.low_index
                    detector.direction = 1
                    detector.high = current_close
                    detector.high_index = index
            if not signal_side:
                continue
            detector.sequence += 1
            raw_events[detector.multiplier] += 1
            if int(features["vol_level"][index]) < 0:
                excluded_regime += 1
                continue
            trade = evaluate_trade(signal_side, index, extreme, market, config)
            if trade is None:
                if index + 1 + int(config["trade"]["maximum_holding_minutes"]) >= len(bars):
                    excluded_horizon += 1
                else:
                    excluded_geometry += 1
                continue
            vol_name = str(names_vol[int(features["vol_level"][index])])
            structure_name = str(names_structure[int(features["structure"][index])])
            theta_bp = detector.multiplier * sigma_bp
            row = {
                "symbol": symbol,
                "detector_multiplier": detector.multiplier,
                "detector_sequence": detector.sequence,
                "side": signal_side,
                "signal_index": index,
                "signal_timestamp_ms": int(timestamps[index]),
                "signal_close": current_close,
                "extreme_index": extreme_index,
                "extreme_timestamp_ms": int(timestamps[extreme_index]),
                "extreme_price": extreme,
                "sigma_bp": sigma_bp,
                "theta_bp": theta_bp,
                "rv240_bp": float(features["rv240_bp"][index]),
                "return240_bp": float(features["return240_bp"][index]),
                "trend_score_abs": float(features["trend_score"][index]),
                "vol_level": vol_name,
                "structure": structure_name,
                "regime": f"{vol_name}_{structure_name}",
                **trade,
            }
            row["week_cluster"] = int(row["entry_timestamp_ms"]) // WEEK_MS
            row["halfyear"] = halfyear(int(row["entry_timestamp_ms"]))
            rows.append(row)
    frame = pd.DataFrame(rows)
    summary = {
        "symbol": symbol,
        "bars": len(bars),
        "first_timestamp_ms": int(timestamps[0]),
        "last_timestamp_ms": int(timestamps[-1]),
        "raw_events_by_multiplier": {str(k): int(v) for k, v in sorted(raw_events.items())},
        "raw_events": int(sum(raw_events.values())),
        "stored_candidates": len(frame),
        "excluded_before_regime_history": excluded_regime,
        "excluded_insufficient_future_horizon": excluded_horizon,
        "excluded_invalid_trade_geometry": excluded_geometry,
    }
    return frame, summary


def write_candidate_database(path: Path, candidates: pd.DataFrame, metadata: dict[str, Any]) -> None:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        candidates.to_sql("candidates", connection, index=False, chunksize=10_000)
        connection.execute("CREATE UNIQUE INDEX idx_candidate_id ON candidates(candidate_id)")
        connection.execute("CREATE INDEX idx_candidate_time ON candidates(symbol,entry_timestamp_ms)")
        connection.execute("CREATE INDEX idx_candidate_cell ON candidates(regime,detector_multiplier,entry_timestamp_ms)")
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        for key, value in metadata.items():
            connection.execute(
                "INSERT INTO metadata VALUES (?,?)",
                (key, json.dumps(value, separators=(",", ":"), allow_nan=False, default=json_default)),
            )
        connection.commit()
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {result}")
    finally:
        connection.close()


def gzip_file(source: Path, target: Path) -> None:
    with source.open("rb") as src, gzip.GzipFile(
        filename=str(target), mode="wb", compresslevel=6, mtime=0
    ) as dst:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(block)


def select_nonoverlap(frame: pd.DataFrame) -> pd.DataFrame:
    keep: list[int] = []
    for _, group in frame.sort_values(["symbol", "entry_timestamp_ms", "candidate_id"]).groupby(
        "symbol", sort=False
    ):
        busy = -1
        for index, entry, until in zip(
            group.index,
            group["entry_timestamp_ms"].to_numpy(np.int64),
            group["busy_until_ms"].to_numpy(np.int64),
        ):
            if int(entry) < busy:
                continue
            keep.append(index)
            busy = int(until)
    return frame.loc[keep].sort_values(["symbol", "entry_timestamp_ms", "candidate_id"]).copy()


def cell_metrics(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    values = frame["net_return_bp"].to_numpy(float)
    n = len(values)
    mean = float(values.mean()) if n else None
    weekly = frame.groupby("week_cluster", sort=True)["net_return_bp"].agg(["count", "sum"])
    clusters = len(weekly)
    if n > 1 and clusters > 1 and mean is not None:
        residual = weekly["sum"].to_numpy(float) - weekly["count"].to_numpy(float) * mean
        se = math.sqrt(clusters / (clusters - 1) * float(np.square(residual).sum())) / n
    else:
        se = math.nan
    half = frame.groupby("halfyear", sort=True)["net_return_bp"].agg(["count", "mean"])
    minimum_half = int(config["evidence_gate"]["minimum_trades_per_halfyear"])
    evaluated = half.loc[half["count"] >= minimum_half]
    counts_symbol = frame["symbol"].value_counts()
    counts_side = frame["side"].value_counts()
    return {
        "observations": n,
        "mean_net_return_bp": mean,
        "profit_factor": profit_factor(values) if n else None,
        "tp_first_rate": float((frame["outcome"] == "tp_first").mean()) if n else None,
        "weekly_clusters": clusters,
        "weekly_cluster_se_bp": se if math.isfinite(se) else None,
        "training_halfyears": int(len(half)),
        "evaluated_halfyears": int(len(evaluated)),
        "minimum_halfyear_trades": int(half["count"].min()) if len(half) else 0,
        "positive_halfyears": int((evaluated["mean"] > 0).sum()),
        "positive_halfyear_fraction": float((evaluated["mean"] > 0).mean()) if len(evaluated) else 0.0,
        "eth_trades": int(counts_symbol.get("ETHUSDT", 0)),
        "xrp_trades": int(counts_symbol.get("XRPUSDT", 0)),
        "long_trades": int(counts_side.get(1, 0)),
        "short_trades": int(counts_side.get(-1, 0)),
    }


def max_t_adjust(
    response: pd.DataFrame,
    economic_by_cell: dict[tuple[str, float], pd.DataFrame],
    config: dict[str, Any],
    seed_offset: int,
) -> pd.DataFrame:
    all_weeks = sorted(
        {
            int(week)
            for frame in economic_by_cell.values()
            for week in frame["week_cluster"].unique()
        }
    )
    if not all_weeks:
        response["max_t_familywise_p"] = 1.0
        response["simultaneous_lower_95_bp"] = None
        response["max_t_critical_95"] = None
        return response
    week_min, week_max = min(all_weeks), max(all_weeks)
    weeks = np.arange(week_min, week_max + 1, dtype=np.int64)
    week_index = {int(value): i for i, value in enumerate(weeks)}
    cells = [(str(row.regime), float(row.detector_multiplier)) for row in response.itertuples()]
    centered = np.zeros((len(weeks), len(cells)), float)
    counts = np.zeros((len(weeks), len(cells)), float)
    ses = response["weekly_cluster_se_bp"].fillna(np.nan).to_numpy(float)
    means = response["mean_net_return_bp"].fillna(np.nan).to_numpy(float)
    for column, cell in enumerate(cells):
        frame = economic_by_cell[cell]
        mean = means[column]
        if frame.empty or not math.isfinite(mean):
            continue
        weekly = frame.groupby("week_cluster")["net_return_bp"].agg(["count", "sum"])
        for week, row in weekly.iterrows():
            position = week_index[int(week)]
            counts[position, column] = float(row["count"])
            centered[position, column] = float(row["sum"]) - float(row["count"]) * mean
    valid = np.isfinite(ses) & (ses > 0) & np.isfinite(means)
    observed_t = np.full(len(cells), -np.inf)
    observed_t[valid] = means[valid] / ses[valid]
    inference = config["inference"]
    reps = int(inference["replicates"])
    block = int(inference["block_weeks"])
    rng = np.random.default_rng(int(inference["random_seed"]) + seed_offset)
    maxima = np.full(reps, -np.inf)
    blocks_needed = math.ceil(len(weeks) / block)
    offsets = np.arange(block, dtype=np.int64)
    chunk = 250
    for start in range(0, reps, chunk):
        size = min(chunk, reps - start)
        starts = rng.integers(0, len(weeks), size=(size, blocks_needed))
        indices = ((starts[:, :, None] + offsets[None, None, :]) % len(weeks)).reshape(size, -1)
        indices = indices[:, : len(weeks)]
        sampled_sum = centered[indices]
        sampled_count = counts[indices]
        boot_sum = sampled_sum.sum(axis=1)
        boot_count = sampled_count.sum(axis=1)
        boot_mean = np.divide(
            boot_sum,
            boot_count,
            out=np.full_like(boot_sum, np.nan),
            where=boot_count > 0,
        )
        residual = sampled_sum - sampled_count * boot_mean[:, None, :]
        boot_se = np.divide(
            np.sqrt(len(weeks) / (len(weeks) - 1) * np.square(residual).sum(axis=1)),
            boot_count,
            out=np.full_like(boot_mean, np.nan),
            where=boot_count > 0,
        )
        boot_t = np.divide(
            boot_mean,
            boot_se,
            out=np.full_like(boot_mean, -np.inf),
            where=np.isfinite(boot_se) & (boot_se > 0) & valid[None, :],
        )
        maxima[start : start + size] = np.max(boot_t, axis=1)
    critical = float(np.quantile(maxima[np.isfinite(maxima)], 0.95))
    pvalues = np.ones(len(cells), float)
    lower = np.full(len(cells), np.nan)
    for column in range(len(cells)):
        if valid[column]:
            pvalues[column] = float((1 + np.sum(maxima >= observed_t[column])) / (reps + 1))
            lower[column] = means[column] - critical * ses[column]
    response["max_t_familywise_p"] = pvalues
    response["simultaneous_lower_95_bp"] = lower
    response["max_t_critical_95"] = critical
    return response


def training_response(
    training: pd.DataFrame, config: dict[str, Any], fold_index: int
) -> tuple[pd.DataFrame, dict[tuple[str, float], pd.DataFrame]]:
    rows: list[dict[str, Any]] = []
    economic: dict[tuple[str, float], pd.DataFrame] = {}
    # A multiplier is one independent economic event stream.  Establish its
    # non-overlapping trade sequence first, then attribute those trades to the
    # causal entry regime.  Selecting overlap separately inside each regime
    # would allow mutually impossible simultaneous positions.
    streams: dict[float, pd.DataFrame] = {}
    for multiplier in config["threshold_multipliers"]:
        stream = training.loc[
            np.isclose(training["detector_multiplier"], float(multiplier))
        ]
        streams[float(multiplier)] = select_nonoverlap(stream) if not stream.empty else stream.copy()
    for regime in config["regimes"]:
        for multiplier in config["threshold_multipliers"]:
            cell = streams[float(multiplier)].loc[
                streams[float(multiplier)]["regime"] == regime
            ].copy()
            key = (regime, float(multiplier))
            economic[key] = cell
            rows.append(
                {
                    "regime": regime,
                    "detector_multiplier": float(multiplier),
                    **cell_metrics(cell, config),
                }
            )
    response = pd.DataFrame(rows)
    response = max_t_adjust(response, economic, config, fold_index * 100_000)
    gate = config["evidence_gate"]
    response["evidence_gate_pass"] = (
        response["observations"].ge(int(gate["minimum_nonoverlap_trades"]))
        & response["weekly_clusters"].ge(int(gate["minimum_calendar_weeks"]))
        & response["training_halfyears"].ge(int(gate["minimum_training_halfyears"]))
        & response["minimum_halfyear_trades"].ge(int(gate["minimum_trades_per_halfyear"]))
        & response["positive_halfyear_fraction"].ge(float(gate["minimum_positive_halfyear_fraction"]))
        & response["eth_trades"].ge(int(gate["minimum_trades_per_asset"]))
        & response["xrp_trades"].ge(int(gate["minimum_trades_per_asset"]))
        & response["long_trades"].ge(int(gate["minimum_trades_per_side"]))
        & response["short_trades"].ge(int(gate["minimum_trades_per_side"]))
        & response["mean_net_return_bp"].gt(0)
        & response["profit_factor"].gt(float(gate["profit_factor_above"]))
        & response["simultaneous_lower_95_bp"].gt(
            float(gate["simultaneous_familywise_lower_95_above_bp"])
        )
    )
    return response, economic


def threshold_map(response: pd.DataFrame, evidence: bool) -> dict[str, float]:
    eligible = response.copy()
    if evidence:
        eligible = eligible.loc[eligible["evidence_gate_pass"]]
    else:
        eligible = eligible.loc[eligible["observations"] >= 200]
    if eligible.empty:
        return {}
    chosen = (
        eligible.sort_values(
            ["regime", "mean_net_return_bp", "simultaneous_lower_95_bp", "observations", "detector_multiplier"],
            ascending=[True, False, False, False, True],
        )
        .groupby("regime", as_index=False)
        .head(1)
    )
    return {str(row.regime): float(row.detector_multiplier) for row in chosen.itertuples()}


def simulate_policy(
    test: pd.DataFrame,
    policy: str,
    fold: str,
    mapping: dict[str, float] | None = None,
    fixed_multiplier: float | None = None,
) -> pd.DataFrame:
    if fixed_multiplier is not None:
        eligible = test.loc[np.isclose(test["detector_multiplier"], fixed_multiplier)].copy()
    else:
        if not mapping:
            return pd.DataFrame(columns=list(test.columns) + ["policy", "fold"])
        required = test["regime"].map(mapping)
        eligible = test.loc[
            required.notna()
            & np.isclose(test["detector_multiplier"].to_numpy(float), required.fillna(-999).to_numpy(float))
        ].copy()
    selected: list[int] = []
    for _, group in eligible.sort_values(["symbol", "entry_timestamp_ms", "candidate_id"]).groupby(
        "symbol", sort=False
    ):
        busy = -1
        for index, entry, until in zip(
            group.index,
            group["entry_timestamp_ms"].to_numpy(np.int64),
            group["busy_until_ms"].to_numpy(np.int64),
        ):
            if int(entry) < busy:
                continue
            selected.append(index)
            busy = int(until)
    output = eligible.loc[selected].sort_values(["symbol", "entry_timestamp_ms", "candidate_id"]).copy()
    output["policy"] = policy
    output["fold"] = fold
    return output


def summary_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    values = frame["net_return_bp"].to_numpy(float) if len(frame) else np.array([], float)
    return {
        "trades": len(frame),
        "mean_net_return_bp": float(values.mean()) if len(values) else None,
        "total_net_return_bp": float(values.sum()) if len(values) else 0.0,
        "profit_factor": profit_factor(values) if len(values) else None,
        "positive": bool(len(values) and values.mean() > 0),
        "mean_gross_after_slippage_bp": float(frame["gross_after_slippage_bp"].mean()) if len(frame) else None,
        "mean_fee_bp": float(frame["fee_bp"].mean()) if len(frame) else None,
        "mean_funding_bp": float(frame["funding_bp"].mean()) if len(frame) else None,
        "tp_first_rate": float((frame["outcome"] == "tp_first").mean()) if len(frame) else None,
        "sl_first_rate": float((frame["outcome"] == "sl_first").mean()) if len(frame) else None,
        "timeout_rate": float((frame["outcome"] == "timeout").mean()) if len(frame) else None,
        "mean_signal_path_gross_bp": float(frame["signal_path_gross_bp"].mean()) if len(frame) else None,
        "signal_path_profit_factor": profit_factor(frame["signal_path_gross_bp"].to_numpy(float)) if len(frame) else None,
        "mean_half_execution_cost_net_bp": float(frame["half_execution_cost_net_bp"].mean()) if len(frame) else None,
        "half_execution_cost_profit_factor": profit_factor(frame["half_execution_cost_net_bp"].to_numpy(float)) if len(frame) else None,
        "mean_execution_cost_bp": float(frame["execution_cost_bp"].mean()) if len(frame) else None,
    }


def empty_summary() -> dict[str, Any]:
    return {
        "trades": 0,
        "mean_net_return_bp": None,
        "total_net_return_bp": 0.0,
        "profit_factor": None,
        "positive": False,
        "mean_gross_after_slippage_bp": None,
        "mean_fee_bp": None,
        "mean_funding_bp": None,
        "tp_first_rate": None,
        "sl_first_rate": None,
        "timeout_rate": None,
        "mean_signal_path_gross_bp": None,
        "signal_path_profit_factor": None,
        "mean_half_execution_cost_net_bp": None,
        "half_execution_cost_profit_factor": None,
        "mean_execution_cost_bp": None,
    }


def grouped_summary(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if frame.empty:
        return pd.DataFrame()
    for key, group in frame.groupby(columns, sort=True, observed=True):
        key = key if isinstance(key, tuple) else (key,)
        rows.append({**dict(zip(columns, key)), **summary_metrics(group)})
    return pd.DataFrame(rows)


def walk_forward(
    candidates: pd.DataFrame, config: dict[str, Any], results_dir: Path
) -> dict[str, pd.DataFrame]:
    all_trades: list[pd.DataFrame] = []
    responses: list[pd.DataFrame] = []
    map_rows: list[dict[str, Any]] = []
    for fold_index, fold in enumerate(config["walk_forward_folds"]):
        start = iso_ms(fold["start"])
        end = iso_ms(fold["end"])
        training = candidates.loc[candidates["exit_timestamp_ms"] < start]
        test = candidates.loc[
            (candidates["entry_timestamp_ms"] >= start)
            & (candidates["exit_timestamp_ms"] < end)
        ]
        print(
            f"[{fold['name']}] training candidates={len(training):,}; test candidates={len(test):,}",
            flush=True,
        )
        response, _ = training_response(training, config, fold_index)
        response.insert(0, "fold", fold["name"])
        responses.append(response)
        evidence_map = threshold_map(response, evidence=True)
        forced_map = threshold_map(response, evidence=False)
        for policy, mapping in (("regime_evidence", evidence_map), ("regime_forced", forced_map)):
            for regime in config["regimes"]:
                map_rows.append(
                    {
                        "fold": fold["name"],
                        "policy": policy,
                        "regime": regime,
                        "detector_multiplier": mapping.get(regime),
                        "no_trade": regime not in mapping,
                    }
                )
            all_trades.append(simulate_policy(test, policy, fold["name"], mapping=mapping))
        for multiplier in config["threshold_multipliers"]:
            policy = f"fixed_k_{float(multiplier):g}"
            all_trades.append(
                simulate_policy(test, policy, fold["name"], fixed_multiplier=float(multiplier))
            )
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    response = pd.concat(responses, ignore_index=True)
    maps = pd.DataFrame(map_rows)
    fold_summary = grouped_summary(trades, ["policy", "fold"])
    aggregate = grouped_summary(trades, ["policy"])
    expected_policies = ["regime_evidence", "regime_forced"] + [
        f"fixed_k_{float(value):g}" for value in config["threshold_multipliers"]
    ]
    missing_aggregate = [
        {"policy": policy, **empty_summary()}
        for policy in expected_policies
        if aggregate.empty or policy not in set(aggregate["policy"])
    ]
    if missing_aggregate:
        aggregate = pd.concat([aggregate, pd.DataFrame(missing_aggregate)], ignore_index=True)
    missing_folds = []
    observed_fold_keys = set(zip(fold_summary.get("policy", []), fold_summary.get("fold", [])))
    for policy in expected_policies:
        for fold in [item["name"] for item in config["walk_forward_folds"]]:
            if (policy, fold) not in observed_fold_keys:
                missing_folds.append({"policy": policy, "fold": fold, **empty_summary()})
    if missing_folds:
        fold_summary = pd.concat([fold_summary, pd.DataFrame(missing_folds)], ignore_index=True)
    aggregate = aggregate.sort_values("policy").reset_index(drop=True)
    fold_summary = fold_summary.sort_values(["policy", "fold"]).reset_index(drop=True)
    side = grouped_summary(trades, ["policy", "side"])
    regime = grouped_summary(trades, ["policy", "regime"])
    regime_side = grouped_summary(trades, ["policy", "regime", "side"])
    symbol_side = grouped_summary(trades, ["policy", "symbol", "side"])
    response.to_csv(results_dir / "phase5_training_response_by_fold.csv", index=False)
    maps.to_csv(results_dir / "phase5_walkforward_threshold_maps.csv", index=False)
    trades.to_csv(
        results_dir / "phase5_walkforward_trades.csv.gz",
        index=False,
        compression={"method": "gzip", "compresslevel": 6, "mtime": 0},
    )
    fold_summary.to_csv(results_dir / "phase5_walkforward_fold_summary.csv", index=False)
    aggregate.to_csv(results_dir / "phase5_walkforward_aggregate.csv", index=False)
    side.to_csv(results_dir / "phase5_walkforward_side_diagnostic.csv", index=False)
    regime.to_csv(results_dir / "phase5_walkforward_regime_diagnostic.csv", index=False)
    regime_side.to_csv(results_dir / "phase5_walkforward_regime_side_diagnostic.csv", index=False)
    symbol_side.to_csv(results_dir / "phase5_walkforward_symbol_side_diagnostic.csv", index=False)
    return {
        "response": response,
        "maps": maps,
        "trades": trades,
        "fold_summary": fold_summary,
        "aggregate": aggregate,
        "side": side,
        "regime": regime,
        "regime_side": regime_side,
        "symbol_side": symbol_side,
    }


def drift_table(data_dir: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for symbol in ("ETHUSDT", "XRPUSDT"):
        for year in (2023, 2024):
            frame = pd.read_csv(
                data_dir / f"{symbol}_1m_{year}.csv.zip",
                compression="zip",
                usecols=["close"],
            )
            output.append(
                {
                    "symbol": symbol,
                    "year": year,
                    "first_close": float(frame["close"].iloc[0]),
                    "last_close": float(frame["close"].iloc[-1]),
                    "return_pct": float((frame["close"].iloc[-1] / frame["close"].iloc[0] - 1) * 100),
                }
            )
    return output


def run(args: argparse.Namespace) -> None:
    began = time.monotonic()
    root = Path(args.output_dir).resolve()
    results_dir = root / "results"
    root.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    config_path = Path(args.config).resolve()
    config = load_json(config_path)
    data_dir = Path(args.data_dir).resolve()
    reference = Path(args.reference_audit).resolve()
    print("Auditing exact Bybit source files", flush=True)
    sources, candles = audit_sources(data_dir, reference, results_dir)
    frames: list[pd.DataFrame] = []
    collection: dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "created_utc": utc_now(),
        "source_files_checked": len(sources),
        "candle_rows_checked": int(candles["rows"].sum()),
        "symbols": {},
        "locked_2025_2026_price_rows_read": 0,
    }
    for symbol in config["symbols"]:
        print(f"[{symbol}] collecting independent dynamic-threshold streams", flush=True)
        frame, summary = collect_symbol(data_dir, symbol, config)
        frames.append(frame)
        collection["symbols"][symbol] = summary
        print(f"[{symbol}] stored {len(frame):,} candidates", flush=True)
    candidates = pd.concat(frames, ignore_index=True)
    candidates.insert(0, "candidate_id", np.arange(1, len(candidates) + 1, dtype=np.int64))
    collection["candidate_rows"] = len(candidates)
    collection["candidate_rows_by_symbol"] = candidates["symbol"].value_counts().sort_index().to_dict()
    collection["candidate_rows_in_2025_or_later"] = int(
        (candidates["entry_timestamp_ms"] >= iso_ms(config["locked_test_start"])).sum()
    )
    if collection["candidate_rows_in_2025_or_later"] != 0:
        raise RuntimeError("Locked 2025+ rows were read")
    write_json(results_dir / "phase5_collection_summary.json", collection)
    database = root / "phase5_candidates_2021_2024.sqlite"
    metadata = {
        "engine_version": ENGINE_VERSION,
        "config_sha256": sha256(config_path),
        "config": config,
        "collection_summary": collection,
        "source_hashes": dict(zip(sources["file"], sources["sha256"])),
    }
    print(f"Writing {len(candidates):,} verified candidate rows", flush=True)
    write_candidate_database(database, candidates, metadata)
    gzip_file(database, root / "phase5_candidates_2021_2024.sqlite.gz")
    print("Running expanding walk-forward selection and 10,000-replicate max-t inference", flush=True)
    outputs = walk_forward(candidates, config, results_dir)
    response = outputs["response"]
    aggregate = outputs["aggregate"]
    fold_evidence_passes = int(response["evidence_gate_pass"].sum())
    evidence_trades = int(
        outputs["trades"].loc[outputs["trades"]["policy"] == "regime_evidence"].shape[0]
    ) if not outputs["trades"].empty else 0
    print("Running final through-2024 family-wise evidence gate", flush=True)
    final_response, _ = training_response(candidates, config, len(config["walk_forward_folds"]))
    final_response.to_csv(results_dir / "phase5_final_training_response.csv", index=False)
    final_evidence_map = threshold_map(final_response, evidence=True)
    final_map_rows = pd.DataFrame(
        [
            {
                "regime": regime,
                "detector_multiplier": final_evidence_map.get(regime),
                "no_trade": regime not in final_evidence_map,
            }
            for regime in config["regimes"]
        ]
    )
    final_map_rows.to_csv(results_dir / "phase5_final_2025_threshold_map.csv", index=False)
    final_evidence_passes = int(final_response["evidence_gate_pass"].sum())
    decision = "NO_TRADE_TERMINATE_RESEARCH_LINE" if not final_evidence_map else "CANDIDATE_AVAILABLE_FREEZE_BEFORE_2025"

    def aggregate_policy(name: str) -> dict[str, Any]:
        row = aggregate.loc[aggregate["policy"] == name]
        return clean_records(row)[0] if len(row) else {"policy": name, **empty_summary()}

    forced = aggregate_policy("regime_forced")
    fixed = aggregate_policy("fixed_k_1.25")
    forced_mean = forced.get("mean_net_return_bp")
    fixed_mean = fixed.get("mean_net_return_bp")
    regime_delta = (
        float(forced_mean) - float(fixed_mean)
        if forced_mean is not None and fixed_mean is not None
        else None
    )
    central_hypothesis_supported = bool(
        regime_delta is not None
        and regime_delta > 0
        and final_evidence_map
    )
    final = {
        "engine_version": ENGINE_VERSION,
        "created_utc": utc_now(),
        "config_sha256": sha256(config_path),
        "data_integrity": {
            "source_files_checked": len(sources),
            "source_files_passed": int(sources["passed"].sum()),
            "candle_rows_checked": int(candles["rows"].sum()),
            "candidate_rows": len(candidates),
            "candidate_rows_by_symbol": candidates["symbol"].value_counts().sort_index().to_dict(),
            "locked_2025_2026_price_rows_read": 0,
            "sqlite_integrity": "ok",
        },
        "method": {
            "regimes": config["regimes"],
            "threshold_multipliers": config["threshold_multipliers"],
            "training_cells_per_fold": len(config["regimes"]) * len(config["threshold_multipliers"]),
            "walk_forward_folds": [item["name"] for item in config["walk_forward_folds"]],
            "bootstrap": config["inference"],
            "evidence_gate": config["evidence_gate"],
        },
        "result": {
            "training_cells": len(response),
            "nominal_positive_training_cells": int((response["mean_net_return_bp"] > 0).sum()),
            "walkforward_training_familywise_evidence_passes": fold_evidence_passes,
            "final_training_cells": len(final_response),
            "final_nominal_positive_training_cells": int((final_response["mean_net_return_bp"] > 0).sum()),
            "final_familywise_evidence_passes": final_evidence_passes,
            "final_evidence_map": final_evidence_map,
            "evidence_policy_trades": evidence_trades,
            "decision": decision,
            "research_line_terminated": not bool(final_evidence_map),
        },
        "central_hypothesis": {
            "statement": "Regime-conditioned threshold selection should improve upon fixed k=1.25.",
            "regime_forced_mean_net_bp": forced_mean,
            "fixed_k_1_25_mean_net_bp": fixed_mean,
            "regime_minus_fixed_mean_net_bp": regime_delta,
            "supported": central_hypothesis_supported,
            "interpretation": "Support requires regime selection to beat the fixed baseline and a non-empty final evidence-gated regime map.",
        },
        "walk_forward_aggregate": clean_records(aggregate),
        "walk_forward_fold_summary": clean_records(outputs["fold_summary"]),
        "side_diagnostic": clean_records(outputs["side"]),
        "regime_side_diagnostic": clean_records(outputs["regime_side"]),
        "drift_2023_2024": drift_table(data_dir),
        "locked_test_access": {
            "opened": False,
            "reason": (
                "Locked pending a frozen final evidence map"
                if final_evidence_map
                else "No final through-2024 regime-threshold cell passed the unchanged family-wise evidence gate"
            ),
        },
        "elapsed_seconds": time.monotonic() - began,
    }
    write_json(results_dir / "PHASE5_REGIME_NORMALIZED_RESULTS.json", final)
    print(
        f"Complete: candidates={len(candidates):,}; training cells={len(response)}; "
        f"final evidence passes={final_evidence_passes}; decision={final['result']['decision']}",
        flush=True,
    )


def self_test() -> None:
    assert tick_round(100.001, 0.01, 1) == 100.01
    assert tick_round(100.009, 0.01, -1) == 100.0
    toy = pd.DataFrame(
        {
            "symbol": ["A", "A", "A"],
            "entry_timestamp_ms": [0, 60_000, 180_000],
            "busy_until_ms": [120_000, 180_000, 240_000],
            "candidate_id": [1, 2, 3],
        }
    )
    chosen = select_nonoverlap(toy)
    assert chosen["candidate_id"].tolist() == [1, 3]
    config = {
        "trade": {"maximum_holding_minutes": 3, "reward_risk": 1.5},
        "execution": {
            "entry_slippage_bp": 0.0,
            "exit_slippage_bp": 0.0,
            "taker_fee_bp_per_fill": 0.0,
        },
    }
    market = {
        "timestamps": np.arange(6, dtype=np.int64) * MINUTE_MS,
        "opens": np.full(6, 100.0),
        "highs": np.array([100.0, 100.5, 101.6, 101.0, 101.0, 101.0]),
        "lows": np.array([100.0, 99.5, 99.5, 99.5, 99.5, 99.5]),
        "closes": np.full(6, 100.0),
        "tick": 0.1,
        "funding_times": np.array([], dtype=np.int64),
        "funding_prefix": np.array([0.0]),
    }
    trade = evaluate_trade(1, 0, 99.0, market, config)
    assert trade is not None
    assert trade["target_price"] == 101.5
    assert trade["outcome"] == "tp_first"
    assert trade["exit_minutes"] == 2
    tie_market = dict(market)
    tie_market["highs"] = np.array([100.0, 101.6, 101.0, 101.0, 101.0, 101.0])
    tie_market["lows"] = np.array([100.0, 98.9, 99.5, 99.5, 99.5, 99.5])
    tie = evaluate_trade(1, 0, 99.0, tie_market, config)
    assert tie is not None and tie["outcome"] == "sl_first" and tie["ambiguous_same_minute"] == 1
    print("Self-test: PASS")


def parser() -> argparse.ArgumentParser:
    output = argparse.ArgumentParser(description=__doc__)
    sub = output.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--data-dir", required=True)
    run_parser.add_argument("--reference-audit", required=True)
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--output-dir", required=True)
    sub.add_parser("self-test")
    return output


if __name__ == "__main__":
    args = parser().parse_args()
    if args.command == "self-test":
        self_test()
    else:
        run(args)
