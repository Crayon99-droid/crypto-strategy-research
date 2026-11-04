#!/usr/bin/env python3
"""Causal Bybit directional-change research engine.

The engine intentionally separates research into two passes:

``collect``
    Walk forward through verified one-minute Bybit data, run independent
    directional-change detectors, create one candidate row per confirmed
    reversal, capture features known at the signal close, enter at the next
    bar open, and store future first-passage information for later labels.

``analyze``
    Reuse the stored candidates to evaluate many stop, reward/risk, horizon,
    and volatility conditions without rescanning the raw candles.  This pass
    writes aggregate tables back into SQLite and a compact JSON report.

This is a research data generator, not a live trading strategy.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import sqlite3
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np
import pandas as pd


ENGINE_VERSION = "1.0.0"


def utc_now() -> str:
    return pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def json_compact(value: Sequence[Any]) -> str:
    return json.dumps(list(value), separators=(",", ":"), allow_nan=False)


def parse_json_array(value: str) -> list[Any]:
    return json.loads(value)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    thresholds = config["detector_thresholds_bp"]
    if thresholds != sorted(set(thresholds)):
        raise ValueError("detector_thresholds_bp must be sorted and unique")
    horizons = config["outcome_horizons_minutes"]
    if max(horizons) != config["maximum_outcome_horizon_minutes"]:
        raise ValueError("maximum horizon must equal the largest outcome horizon")
    if config["execution"]["same_bar_tp_sl_policy"] != "stop_first":
        raise ValueError("v1 supports only the conservative stop_first policy")
    return config


def resolve_year_zip(data_dir: Path, symbol: str, year: int) -> Path:
    candidates = [
        Path(item)
        for item in glob.glob(str(data_dir / f"{symbol}_1m_{year}.csv*.zip"))
    ]
    if not candidates:
        raise FileNotFoundError(f"No {symbol} {year} archive in {data_dir}")
    return max(candidates, key=lambda item: item.stat().st_size)


def years_in_range(start: str, end: str) -> range:
    start_year = pd.Timestamp(start).year
    # The research interval is [start, end), so midnight on 1 January must not
    # load the new year's archive.
    last_year = (pd.Timestamp(end) - pd.Timedelta(microseconds=1)).year
    return range(start_year, last_year + 1)


def load_bars(data_dir: Path, symbol: str, start: str, end: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    columns = [
        "timestamp_utc",
        "timestamp_ms",
        "open",
        "high",
        "low",
        "close",
        "volume_base",
        "turnover_usdt",
    ]
    for year in years_in_range(start, end):
        try:
            path = resolve_year_zip(data_dir, symbol, year)
        except FileNotFoundError:
            continue
        frame = pd.read_csv(path, compression="zip", usecols=columns)
        frame = frame[
            (frame["timestamp_utc"] >= start) & (frame["timestamp_utc"] < end)
        ]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise RuntimeError(f"No {symbol} bars in {start} to {end}")
    data = pd.concat(frames, ignore_index=True)
    if not data["timestamp_ms"].is_monotonic_increasing:
        raise ValueError(f"{symbol} timestamps are not increasing")
    differences = data["timestamp_ms"].diff().dropna()
    if not bool((differences == 60_000).all()):
        raise ValueError(f"{symbol} contains missing or duplicate minutes")
    return data


def load_funding(data_dir: Path, symbol: str) -> pd.DataFrame:
    candidates = list(data_dir.glob(f"{symbol}_funding*.csv"))
    if not candidates:
        raise FileNotFoundError(f"No {symbol} funding history")
    path = max(candidates, key=lambda item: item.stat().st_size)
    frame = pd.read_csv(path)
    return frame.sort_values("timestamp_ms", kind="stable").reset_index(drop=True)


def load_instrument(data_dir: Path, symbol: str) -> dict[str, Any]:
    candidates = list(data_dir.glob(f"{symbol}_instrument*.json"))
    if not candidates:
        raise FileNotFoundError(f"No {symbol} instrument specification")
    path = max(candidates, key=lambda item: item.stat().st_size)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "file": path.name,
        "sha256": sha256(path),
        "symbol": payload["symbol"],
        "tick_size": float(payload["priceFilter"]["tickSize"]),
        "qty_step": float(payload["lotSizeFilter"]["qtyStep"]),
        "min_qty": float(payload["lotSizeFilter"]["minOrderQty"]),
        "min_notional": float(payload["lotSizeFilter"]["minNotionalValue"]),
        "funding_interval_minutes": int(payload["fundingInterval"]),
    }


def rolling_zscore(series: pd.Series, window: int) -> np.ndarray:
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=1)
    return ((series - mean) / std.replace(0.0, np.nan)).to_numpy(dtype=float)


def calculate_features(bars: pd.DataFrame, funding: pd.DataFrame) -> dict[str, np.ndarray]:
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    volume = bars["volume_base"].astype(float)
    log_return = np.log(close).diff()

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    output: dict[str, np.ndarray] = {}
    for window in (15, 60, 240):
        output[f"rv{window}_bp"] = (
            log_return.rolling(window, min_periods=window).std(ddof=1)
            * math.sqrt(window)
            * 10_000.0
        ).to_numpy(dtype=float)
    for window in (14, 60):
        output[f"atr{window}_bp"] = (
            true_range.rolling(window, min_periods=window).mean()
            / close
            * 10_000.0
        ).to_numpy(dtype=float)
    for window in (1, 5, 15, 60, 240):
        output[f"return{window}_bp"] = (
            (close / close.shift(window) - 1.0) * 10_000.0
        ).to_numpy(dtype=float)
    for window in (20, 60):
        output[f"volume_z{window}"] = rolling_zscore(volume, window)

    sma60 = close.rolling(60, min_periods=60).mean()
    sma240 = close.rolling(240, min_periods=240).mean()
    output["sma60_distance_bp"] = ((close / sma60 - 1.0) * 10_000.0).to_numpy()
    output["sma240_distance_bp"] = ((close / sma240 - 1.0) * 10_000.0).to_numpy()
    output["sma60_slope15_bp"] = (
        (sma60 / sma60.shift(15) - 1.0) * 10_000.0
    ).to_numpy()
    output["bar_range_bp"] = ((high - low) / close * 10_000.0).to_numpy()
    denominator = (high - low).replace(0.0, np.nan)
    output["close_location"] = ((close - low) / denominator).to_numpy()

    timestamps = bars["timestamp_ms"].to_numpy(dtype=np.int64)
    funding_timestamps = funding["timestamp_ms"].to_numpy(dtype=np.int64)
    funding_rates = funding["funding_rate"].to_numpy(dtype=float)
    prior_index = np.searchsorted(funding_timestamps, timestamps, side="right") - 1
    latest = np.full(len(bars), np.nan, dtype=float)
    valid = prior_index >= 0
    latest[valid] = funding_rates[prior_index[valid]]
    output["latest_funding_rate"] = latest

    minute_of_day = ((timestamps // 60_000) % 1_440).astype(np.int16)
    day_of_week = pd.to_datetime(timestamps, unit="ms", utc=True).dayofweek.to_numpy()
    output["minute_of_day_utc"] = minute_of_day
    output["day_of_week_utc"] = day_of_week.astype(np.int8)
    return output


@dataclass
class DetectorState:
    threshold_bp: int
    direction: int = 0
    high: float = math.nan
    low: float = math.nan
    high_index: int = -1
    low_index: int = -1
    leg_start_price: float = math.nan
    leg_start_index: int = -1
    event_sequence: int = 0


@dataclass
class CandidateSeed:
    symbol: str
    threshold_bp: int
    side: int
    signal_index: int
    extreme_index: int
    extreme_price: float
    retracement_bp: float
    prior_leg_return_bp: float
    prior_leg_bars: int
    extreme_age_bars: int
    detector_sequence: int


def generate_candidate_seeds(
    symbol: str,
    bars: pd.DataFrame,
    thresholds_bp: Sequence[int],
) -> list[CandidateSeed]:
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    states = [DetectorState(int(value)) for value in thresholds_bp]
    candidates: list[CandidateSeed] = []

    for index in range(len(bars) - 1):
        current_high = high[index]
        current_low = low[index]
        current_close = close[index]
        for state in states:
            threshold = state.threshold_bp / 10_000.0
            if state.high_index < 0:
                state.high = current_high
                state.low = current_low
                state.high_index = index
                state.low_index = index
                state.leg_start_price = current_close
                state.leg_start_index = index
                continue

            if state.direction == 0:
                if current_high > state.high:
                    state.high = current_high
                    state.high_index = index
                if current_low < state.low:
                    state.low = current_low
                    state.low_index = index
                up_move = current_close / state.low - 1.0
                down_move = 1.0 - current_close / state.high
                if up_move >= threshold and up_move >= down_move:
                    state.direction = 1
                    state.leg_start_price = state.low
                    state.leg_start_index = state.low_index
                    # The reversal is confirmed at the close. Starting from
                    # the close avoids assuming the order of this bar's high
                    # and low, which 1-minute OHLC data cannot reveal.
                    state.high = current_close
                    state.high_index = index
                elif down_move >= threshold:
                    state.direction = -1
                    state.leg_start_price = state.high
                    state.leg_start_index = state.high_index
                    state.low = current_close
                    state.low_index = index
                continue

            if state.direction == 1:
                if current_high > state.high:
                    state.high = current_high
                    state.high_index = index
                retracement = 1.0 - current_close / state.high
                if retracement >= threshold:
                    state.event_sequence += 1
                    prior_leg = (
                        state.high / state.leg_start_price - 1.0
                        if state.leg_start_price > 0.0
                        else math.nan
                    )
                    candidates.append(
                        CandidateSeed(
                            symbol=symbol,
                            threshold_bp=state.threshold_bp,
                            side=-1,
                            signal_index=index,
                            extreme_index=state.high_index,
                            extreme_price=state.high,
                            retracement_bp=retracement * 10_000.0,
                            prior_leg_return_bp=prior_leg * 10_000.0,
                            prior_leg_bars=state.high_index - state.leg_start_index,
                            extreme_age_bars=index - state.high_index,
                            detector_sequence=state.event_sequence,
                        )
                    )
                    old_extreme = state.high
                    old_extreme_index = state.high_index
                    state.direction = -1
                    state.leg_start_price = old_extreme
                    state.leg_start_index = old_extreme_index
                    state.low = current_close
                    state.low_index = index
            else:
                if current_low < state.low:
                    state.low = current_low
                    state.low_index = index
                retracement = current_close / state.low - 1.0
                if retracement >= threshold:
                    state.event_sequence += 1
                    prior_leg = (
                        state.leg_start_price / state.low - 1.0
                        if state.low > 0.0
                        else math.nan
                    )
                    candidates.append(
                        CandidateSeed(
                            symbol=symbol,
                            threshold_bp=state.threshold_bp,
                            side=1,
                            signal_index=index,
                            extreme_index=state.low_index,
                            extreme_price=state.low,
                            retracement_bp=retracement * 10_000.0,
                            prior_leg_return_bp=prior_leg * 10_000.0,
                            prior_leg_bars=state.low_index - state.leg_start_index,
                            extreme_age_bars=index - state.low_index,
                            detector_sequence=state.event_sequence,
                        )
                    )
                    old_extreme = state.low
                    old_extreme_index = state.low_index
                    state.direction = 1
                    state.leg_start_price = old_extreme
                    state.leg_start_index = old_extreme_index
                    state.high = current_close
                    state.high_index = index
    return candidates


def all_barriers(config: dict[str, Any]) -> np.ndarray:
    values = set(float(item) for item in config["stop_distances_bp"])
    for stop in config["stop_distances_bp"]:
        for ratio in config["reward_risk_ratios"]:
            values.add(float(stop) * float(ratio))
    return np.asarray(sorted(values), dtype=float)


def first_passage_minutes(cumulative: np.ndarray, barriers: np.ndarray) -> np.ndarray:
    indices = np.searchsorted(cumulative, barriers, side="left")
    output = np.full(len(barriers), -1, dtype=np.int16)
    valid = indices < len(cumulative)
    output[valid] = indices[valid] + 1
    return output


def finite_or_none(value: Any) -> Any:
    if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def align_price_to_tick(value: float, tick_size: float, direction: int) -> float:
    """Align a price adversely: direction +1 rounds up and -1 rounds down."""
    scaled = value / tick_size
    if direction > 0:
        return math.ceil(scaled - 1e-12) * tick_size
    return math.floor(scaled + 1e-12) * tick_size


def create_database(path: Path, metadata: dict[str, Any]) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
    )
    connection.execute(
        """
        CREATE TABLE candidates (
            candidate_id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            detector_threshold_bp INTEGER NOT NULL,
            detector_sequence INTEGER NOT NULL,
            side INTEGER NOT NULL,
            market_episode_id TEXT NOT NULL,
            signal_index INTEGER NOT NULL,
            entry_index INTEGER NOT NULL,
            signal_timestamp_ms INTEGER NOT NULL,
            entry_timestamp_ms INTEGER NOT NULL,
            extreme_timestamp_ms INTEGER NOT NULL,
            signal_close REAL NOT NULL,
            entry_open REAL NOT NULL,
            entry_price REAL NOT NULL,
            extreme_price REAL NOT NULL,
            retracement_bp REAL NOT NULL,
            prior_leg_return_bp REAL,
            prior_leg_bars INTEGER NOT NULL,
            extreme_age_bars INTEGER NOT NULL,
            rv15_bp REAL,
            rv60_bp REAL,
            rv240_bp REAL,
            atr14_bp REAL,
            atr60_bp REAL,
            return1_bp REAL,
            return5_bp REAL,
            return15_bp REAL,
            return60_bp REAL,
            return240_bp REAL,
            volume_z20 REAL,
            volume_z60 REAL,
            sma60_distance_bp REAL,
            sma240_distance_bp REAL,
            sma60_slope15_bp REAL,
            bar_range_bp REAL,
            close_location REAL,
            latest_funding_rate REAL,
            minute_of_day_utc INTEGER NOT NULL,
            day_of_week_utc INTEGER NOT NULL,
            available_minutes INTEGER NOT NULL,
            favorable_first_minutes_json TEXT NOT NULL,
            adverse_first_minutes_json TEXT NOT NULL,
            favorable_barriers_actual_bp_json TEXT NOT NULL,
            adverse_barriers_actual_bp_json TEXT NOT NULL,
            mfe_horizons_bp_json TEXT NOT NULL,
            mae_horizons_bp_json TEXT NOT NULL,
            close_return_horizons_bp_json TEXT NOT NULL,
            funding_minutes_json TEXT NOT NULL,
            funding_rates_json TEXT NOT NULL
        )
        """
    )
    for key, value in metadata.items():
        connection.execute(
            "INSERT INTO metadata(key,value_json) VALUES (?,?)",
            (key, json.dumps(value, separators=(",", ":"), allow_nan=False)),
        )
    connection.commit()
    return connection


CANDIDATE_INSERT = """
INSERT INTO candidates (
    symbol, detector_threshold_bp, detector_sequence, side, market_episode_id,
    signal_index, entry_index, signal_timestamp_ms, entry_timestamp_ms,
    extreme_timestamp_ms, signal_close, entry_open, entry_price, extreme_price,
    retracement_bp, prior_leg_return_bp, prior_leg_bars, extreme_age_bars,
    rv15_bp, rv60_bp, rv240_bp, atr14_bp, atr60_bp,
    return1_bp, return5_bp, return15_bp, return60_bp, return240_bp,
    volume_z20, volume_z60, sma60_distance_bp, sma240_distance_bp,
    sma60_slope15_bp, bar_range_bp, close_location, latest_funding_rate,
    minute_of_day_utc, day_of_week_utc, available_minutes,
    favorable_first_minutes_json, adverse_first_minutes_json,
    favorable_barriers_actual_bp_json, adverse_barriers_actual_bp_json,
    mfe_horizons_bp_json, mae_horizons_bp_json,
    close_return_horizons_bp_json, funding_minutes_json, funding_rates_json
) VALUES ({})
""".format(",".join("?" for _ in range(48)))


def candidate_row(
    seed: CandidateSeed,
    market: dict[str, np.ndarray],
    features: dict[str, np.ndarray],
    barriers: np.ndarray,
    horizons: Sequence[int],
    maximum_horizon: int,
    entry_slippage_bp: float,
) -> tuple[Any, ...]:
    signal_index = seed.signal_index
    entry_index = signal_index + 1
    timestamps = market["timestamps"]
    opens = market["opens"]
    highs = market["highs"]
    lows = market["lows"]
    closes = market["closes"]

    entry_open = opens[entry_index]
    raw_entry_price = entry_open * (
        1.0 + seed.side * float(entry_slippage_bp) / 10_000.0
    )
    tick_size = float(market["tick_size"])
    entry_price = align_price_to_tick(raw_entry_price, tick_size, seed.side)
    stop_index = min(len(timestamps), entry_index + maximum_horizon)
    future_high = highs[entry_index:stop_index]
    future_low = lows[entry_index:stop_index]
    future_close = closes[entry_index:stop_index]
    available = len(future_close)

    if seed.side == 1:
        favorable = (future_high / entry_price - 1.0) * 10_000.0
        adverse = (1.0 - future_low / entry_price) * 10_000.0
        close_return = (future_close / entry_price - 1.0) * 10_000.0
    else:
        favorable = (1.0 - future_low / entry_price) * 10_000.0
        adverse = (future_high / entry_price - 1.0) * 10_000.0
        close_return = (1.0 - future_close / entry_price) * 10_000.0

    cumulative_favorable = np.maximum.accumulate(favorable)
    cumulative_adverse = np.maximum.accumulate(adverse)
    if seed.side == 1:
        favorable_prices = np.asarray(
            [
                align_price_to_tick(
                    entry_price * (1.0 + barrier / 10_000.0), tick_size, 1
                )
                for barrier in barriers
            ]
        )
        adverse_prices = np.asarray(
            [
                align_price_to_tick(
                    entry_price * (1.0 - barrier / 10_000.0), tick_size, -1
                )
                for barrier in barriers
            ]
        )
        favorable_barriers = (favorable_prices / entry_price - 1.0) * 10_000.0
        adverse_barriers = (1.0 - adverse_prices / entry_price) * 10_000.0
    else:
        favorable_prices = np.asarray(
            [
                align_price_to_tick(
                    entry_price * (1.0 - barrier / 10_000.0), tick_size, -1
                )
                for barrier in barriers
            ]
        )
        adverse_prices = np.asarray(
            [
                align_price_to_tick(
                    entry_price * (1.0 + barrier / 10_000.0), tick_size, 1
                )
                for barrier in barriers
            ]
        )
        favorable_barriers = (1.0 - favorable_prices / entry_price) * 10_000.0
        adverse_barriers = (adverse_prices / entry_price - 1.0) * 10_000.0
    favorable_minutes = first_passage_minutes(
        cumulative_favorable, favorable_barriers
    )
    adverse_minutes = first_passage_minutes(cumulative_adverse, adverse_barriers)

    mfe: list[float | None] = []
    mae: list[float | None] = []
    horizon_close: list[float | None] = []
    for horizon in horizons:
        if available >= horizon:
            mfe.append(float(cumulative_favorable[horizon - 1]))
            mae.append(float(cumulative_adverse[horizon - 1]))
            horizon_close.append(float(close_return[horizon - 1]))
        else:
            mfe.append(None)
            mae.append(None)
            horizon_close.append(None)

    entry_timestamp = int(timestamps[entry_index])
    last_timestamp = int(timestamps[stop_index - 1])
    funding_times = market["funding_times"]
    funding_rates = market["funding_rates"]
    left = int(np.searchsorted(funding_times, entry_timestamp, side="right"))
    right = int(np.searchsorted(funding_times, last_timestamp, side="right"))
    future_funding_times = funding_times[left:right]
    future_funding_rates = funding_rates[left:right]
    funding_minutes = [
        int((value - entry_timestamp) // 60_000 + 1)
        for value in future_funding_times
    ]

    extreme_timestamp = int(timestamps[seed.extreme_index])
    episode = f"{seed.symbol}:{seed.side}:{extreme_timestamp}"
    feature_names = [
        "rv15_bp",
        "rv60_bp",
        "rv240_bp",
        "atr14_bp",
        "atr60_bp",
        "return1_bp",
        "return5_bp",
        "return15_bp",
        "return60_bp",
        "return240_bp",
        "volume_z20",
        "volume_z60",
        "sma60_distance_bp",
        "sma240_distance_bp",
        "sma60_slope15_bp",
        "bar_range_bp",
        "close_location",
        "latest_funding_rate",
    ]
    values = [finite_or_none(features[name][signal_index]) for name in feature_names]
    return (
        seed.symbol,
        seed.threshold_bp,
        seed.detector_sequence,
        seed.side,
        episode,
        signal_index,
        entry_index,
        int(timestamps[signal_index]),
        entry_timestamp,
        extreme_timestamp,
        float(closes[signal_index]),
        float(entry_open),
        float(entry_price),
        float(seed.extreme_price),
        float(seed.retracement_bp),
        finite_or_none(seed.prior_leg_return_bp),
        int(seed.prior_leg_bars),
        int(seed.extreme_age_bars),
        *values,
        int(features["minute_of_day_utc"][signal_index]),
        int(features["day_of_week_utc"][signal_index]),
        available,
        json_compact(int(item) for item in favorable_minutes),
        json_compact(int(item) for item in adverse_minutes),
        json_compact(float(item) for item in favorable_barriers),
        json_compact(float(item) for item in adverse_barriers),
        json_compact(mfe),
        json_compact(mae),
        json_compact(horizon_close),
        json_compact(funding_minutes),
        json_compact(float(item) for item in future_funding_rates),
    )


def collect(
    data_dir: Path,
    config_path: Path,
    database_path: Path,
    symbols: Sequence[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    selected_symbols = list(symbols or config["symbols"])
    start = start or config["discovery_start"]
    end = end or config["discovery_end_exclusive"]
    barriers = all_barriers(config)
    horizons = [int(item) for item in config["outcome_horizons_minutes"]]
    maximum_horizon = int(config["maximum_outcome_horizon_minutes"])
    metadata = {
        "engine_version": ENGINE_VERSION,
        "created_utc": utc_now(),
        "config": config,
        "config_sha256": sha256(config_path),
        "data_dir": str(data_dir.resolve()),
        "symbols": selected_symbols,
        "start": start,
        "end_exclusive": end,
        "barrier_levels_bp": barriers.tolist(),
        "feature_timing": "signal close; entry is next bar open",
        "label_timing": "future bars after entry; not used as features",
    }
    connection = create_database(database_path, metadata)
    summary: dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "start": start,
        "end_exclusive": end,
        "symbols": {},
    }

    try:
        for symbol in selected_symbols:
            began = time.monotonic()
            print(f"[{symbol}] loading bars", flush=True)
            bars = load_bars(data_dir, symbol, start, end)
            funding = load_funding(data_dir, symbol)
            instrument = load_instrument(data_dir, symbol)
            print(f"[{symbol}] calculating past-only features", flush=True)
            features = calculate_features(bars, funding)
            market = {
                "timestamps": bars["timestamp_ms"].to_numpy(dtype=np.int64),
                "opens": bars["open"].to_numpy(dtype=float),
                "highs": bars["high"].to_numpy(dtype=float),
                "lows": bars["low"].to_numpy(dtype=float),
                "closes": bars["close"].to_numpy(dtype=float),
                "funding_times": funding["timestamp_ms"].to_numpy(dtype=np.int64),
                "funding_rates": funding["funding_rate"].to_numpy(dtype=float),
                "tick_size": float(instrument["tick_size"]),
            }
            print(f"[{symbol}] generating directional-change candidates", flush=True)
            seeds = generate_candidate_seeds(
                symbol, bars, config["detector_thresholds_bp"]
            )
            print(f"[{symbol}] labeling {len(seeds):,} candidates", flush=True)
            batch: list[tuple[Any, ...]] = []
            inserted = 0
            for seed in seeds:
                row = candidate_row(
                    seed,
                    market,
                    features,
                    barriers,
                    horizons,
                    maximum_horizon,
                    config["execution"]["entry_slippage_bp"],
                )
                batch.append(row)
                if len(batch) >= 5_000:
                    connection.executemany(CANDIDATE_INSERT, batch)
                    connection.commit()
                    inserted += len(batch)
                    batch.clear()
                    if inserted % 50_000 == 0:
                        print(f"[{symbol}] stored {inserted:,}", flush=True)
            if batch:
                connection.executemany(CANDIDATE_INSERT, batch)
                connection.commit()
                inserted += len(batch)
            counts = {
                str(threshold): sum(
                    1 for seed in seeds if seed.threshold_bp == threshold
                )
                for threshold in config["detector_thresholds_bp"]
            }
            elapsed = time.monotonic() - began
            summary["symbols"][symbol] = {
                "bars": len(bars),
                "candidates": inserted,
                "candidates_by_threshold_bp": counts,
                "instrument": instrument,
                "elapsed_seconds": elapsed,
            }
            print(
                f"[{symbol}] complete: {inserted:,} candidates in {elapsed:.1f}s",
                flush=True,
            )
        connection.execute(
            "CREATE INDEX idx_candidates_symbol_threshold "
            "ON candidates(symbol,detector_threshold_bp)"
        )
        connection.execute(
            "CREATE INDEX idx_candidates_episode ON candidates(market_episode_id)"
        )
        connection.execute(
            "CREATE INDEX idx_candidates_entry_time ON candidates(entry_timestamp_ms)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value_json) VALUES (?,?)",
            ("collection_summary", json.dumps(summary, separators=(",", ":"))),
        )
        connection.commit()
    finally:
        connection.close()
    return summary


@dataclass
class Aggregate:
    observations: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    ambiguous: int = 0
    net_positive: int = 0
    sum_return: float = 0.0
    sum_square_return: float = 0.0
    gross_positive: float = 0.0
    gross_negative: float = 0.0
    sum_exit_minutes: float = 0.0
    cluster_counts: dict[int, int] = field(default_factory=dict)
    cluster_sums: dict[int, float] = field(default_factory=dict)

    def add(
        self,
        outcome: str,
        net_return: float,
        exit_minute: int,
        tie: bool,
        cluster_id: int | None = None,
    ) -> None:
        self.observations += 1
        if outcome == "win":
            self.wins += 1
        elif outcome == "loss":
            self.losses += 1
        else:
            self.timeouts += 1
        if tie:
            self.ambiguous += 1
        if net_return > 0.0:
            self.net_positive += 1
            self.gross_positive += net_return
        elif net_return < 0.0:
            self.gross_negative += -net_return
        self.sum_return += net_return
        self.sum_square_return += net_return * net_return
        self.sum_exit_minutes += exit_minute
        if cluster_id is not None:
            self.cluster_counts[cluster_id] = self.cluster_counts.get(cluster_id, 0) + 1
            self.cluster_sums[cluster_id] = (
                self.cluster_sums.get(cluster_id, 0.0) + net_return
            )

    def metrics(self, include_clusters: bool = True) -> dict[str, Any]:
        n = self.observations
        mean = self.sum_return / n if n else math.nan
        variance = (
            max(0.0, (self.sum_square_return - n * mean * mean) / (n - 1))
            if n > 1
            else math.nan
        )
        standard_error = math.sqrt(variance / n) if n > 1 else math.nan
        output = {
            "observations": n,
            "wins": self.wins,
            "losses": self.losses,
            "timeouts": self.timeouts,
            "ambiguous_same_bar": self.ambiguous,
            "tp_first_rate": self.wins / n if n else None,
            "net_positive_rate": self.net_positive / n if n else None,
            "mean_net_return_bp": mean if n else None,
            "standard_error_bp": standard_error if n > 1 else None,
            "normal_lower_95_bp": mean - 1.96 * standard_error if n > 1 else None,
            "profit_factor": (
                self.gross_positive / self.gross_negative
                if self.gross_negative > 0.0
                else None
            ),
            "average_exit_minutes": self.sum_exit_minutes / n if n else None,
        }
        if include_clusters:
            clusters = len(self.cluster_counts)
            if n > 1 and clusters > 1:
                residual_cluster_sums = [
                    self.cluster_sums[key] - self.cluster_counts[key] * mean
                    for key in self.cluster_counts
                ]
                cluster_se = math.sqrt(
                    clusters
                    / (clusters - 1)
                    * sum(value * value for value in residual_cluster_sums)
                ) / n
            else:
                cluster_se = math.nan
            output.update(
                {
                    "weekly_clusters": clusters,
                    "weekly_cluster_standard_error_bp": (
                        cluster_se if math.isfinite(cluster_se) else None
                    ),
                    "weekly_cluster_lower_95_bp": (
                        mean - 1.96 * cluster_se
                        if n and math.isfinite(cluster_se)
                        else None
                    ),
                }
            )
        return output


def volatility_ratio_bin(threshold_bp: int, rv60_bp: float | None) -> str:
    if rv60_bp is None or not math.isfinite(rv60_bp) or rv60_bp <= 0.0:
        return "missing"
    ratio = threshold_bp / rv60_bp
    if ratio < 0.5:
        return "lt_0.5"
    if ratio < 1.0:
        return "0.5_to_1"
    if ratio < 1.5:
        return "1_to_1.5"
    if ratio < 2.0:
        return "1.5_to_2"
    if ratio < 3.0:
        return "2_to_3"
    if ratio < 5.0:
        return "3_to_5"
    return "ge_5"


def funding_net_bp(
    side: int,
    exit_minute: int,
    funding_minutes: Sequence[int],
    funding_rates: Sequence[float],
) -> float:
    paid_rate = sum(
        rate
        for minute, rate in zip(funding_minutes, funding_rates)
        if minute <= exit_minute
    )
    return -side * paid_rate * 10_000.0


def analyze(database_path: Path, report_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database_path)
    metadata_rows = connection.execute("SELECT key,value_json FROM metadata").fetchall()
    metadata = {key: json.loads(value) for key, value in metadata_rows}
    config = metadata["config"]
    barriers = np.asarray(metadata["barrier_levels_bp"], dtype=float)
    barrier_index = {round(float(value), 8): i for i, value in enumerate(barriers)}
    horizons = [int(item) for item in config["outcome_horizons_minutes"]]
    horizon_index = {value: i for i, value in enumerate(horizons)}
    fee_bp = 2.0 * float(config["execution"]["taker_fee_bp_per_fill"])
    exit_slippage_bp = float(config["execution"]["exit_slippage_bp"])

    grid: dict[tuple[Any, ...], Aggregate] = {}
    volatility_grid: dict[tuple[Any, ...], Aggregate] = {}
    query = """
        SELECT symbol,detector_threshold_bp,side,rv60_bp,available_minutes,
               entry_timestamp_ms,
               favorable_first_minutes_json,adverse_first_minutes_json,
               favorable_barriers_actual_bp_json,
               adverse_barriers_actual_bp_json,
               close_return_horizons_bp_json,funding_minutes_json,
               funding_rates_json
        FROM candidates
        ORDER BY candidate_id
    """
    cursor = connection.execute(query)
    processed = 0
    while True:
        rows = cursor.fetchmany(5_000)
        if not rows:
            break
        for row in rows:
            (
                symbol,
                threshold_bp,
                side,
                rv60_bp,
                available,
                entry_timestamp_ms,
                favorable_json,
                adverse_json,
                favorable_barriers_json,
                adverse_barriers_json,
                close_json,
                funding_minutes_json,
                funding_rates_json,
            ) = row
            favorable = parse_json_array(favorable_json)
            adverse = parse_json_array(adverse_json)
            favorable_barriers = parse_json_array(favorable_barriers_json)
            adverse_barriers = parse_json_array(adverse_barriers_json)
            horizon_close = parse_json_array(close_json)
            funding_minutes = parse_json_array(funding_minutes_json)
            funding_rates = parse_json_array(funding_rates_json)
            regime = volatility_ratio_bin(threshold_bp, rv60_bp)
            # Calendar-week clusters preserve intraday/serial dependence when
            # estimating uncertainty instead of treating overlapping trades
            # as independent observations.
            week_cluster = int(entry_timestamp_ms) // (7 * 24 * 60 * 60 * 1_000)

            for stop in config["stop_distances_bp"]:
                stop_value = float(stop)
                adverse_minute = adverse[barrier_index[round(stop_value, 8)]]
                for ratio in config["reward_risk_ratios"]:
                    ratio_value = float(ratio)
                    target_value = stop_value * ratio_value
                    favorable_minute = favorable[
                        barrier_index[round(target_value, 8)]
                    ]
                    for horizon in horizons:
                        if available < horizon:
                            continue
                        hit_favorable = 0 < favorable_minute <= horizon
                        hit_adverse = 0 < adverse_minute <= horizon
                        tie = (
                            hit_favorable
                            and hit_adverse
                            and favorable_minute == adverse_minute
                        )
                        if hit_adverse and (
                            not hit_favorable or adverse_minute <= favorable_minute
                        ):
                            outcome = "loss"
                            gross_return = -float(
                                adverse_barriers[
                                    barrier_index[round(stop_value, 8)]
                                ]
                            )
                            exit_minute = int(adverse_minute)
                        elif hit_favorable:
                            outcome = "win"
                            gross_return = float(
                                favorable_barriers[
                                    barrier_index[round(target_value, 8)]
                                ]
                            )
                            exit_minute = int(favorable_minute)
                        else:
                            outcome = "timeout"
                            gross_return = float(horizon_close[horizon_index[horizon]])
                            exit_minute = horizon
                        net_return = (
                            gross_return
                            - fee_bp
                            - exit_slippage_bp
                            + funding_net_bp(
                                int(side),
                                exit_minute,
                                funding_minutes,
                                funding_rates,
                            )
                        )
                        key = (
                            symbol,
                            int(threshold_bp),
                            stop_value,
                            ratio_value,
                            int(horizon),
                        )
                        grid.setdefault(key, Aggregate()).add(
                            outcome,
                            net_return,
                            exit_minute,
                            tie,
                            cluster_id=week_cluster,
                        )
                        volatility_key = (*key, regime)
                        volatility_grid.setdefault(volatility_key, Aggregate()).add(
                            outcome, net_return, exit_minute, tie
                        )
        processed += len(rows)
        if processed % 50_000 == 0:
            print(f"analyzed {processed:,} candidates", flush=True)

    connection.execute("DROP TABLE IF EXISTS grid_statistics")
    connection.execute("DROP TABLE IF EXISTS volatility_statistics")
    connection.execute(
        """
        CREATE TABLE grid_statistics (
            symbol TEXT, detector_threshold_bp INTEGER, stop_bp REAL,
            reward_risk REAL, horizon_minutes INTEGER, observations INTEGER,
            wins INTEGER, losses INTEGER, timeouts INTEGER,
            ambiguous_same_bar INTEGER, tp_first_rate REAL,
            net_positive_rate REAL, mean_net_return_bp REAL,
            standard_error_bp REAL, normal_lower_95_bp REAL,
            profit_factor REAL, average_exit_minutes REAL,
            weekly_clusters INTEGER,
            weekly_cluster_standard_error_bp REAL,
            weekly_cluster_lower_95_bp REAL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE volatility_statistics (
            symbol TEXT, detector_threshold_bp INTEGER, stop_bp REAL,
            reward_risk REAL, horizon_minutes INTEGER, volatility_ratio_bin TEXT,
            observations INTEGER, wins INTEGER, losses INTEGER, timeouts INTEGER,
            ambiguous_same_bar INTEGER, tp_first_rate REAL,
            net_positive_rate REAL, mean_net_return_bp REAL,
            standard_error_bp REAL, normal_lower_95_bp REAL,
            profit_factor REAL, average_exit_minutes REAL
        )
        """
    )
    grid_rows: list[tuple[Any, ...]] = []
    for key, aggregate in grid.items():
        metrics = aggregate.metrics()
        grid_rows.append((*key, *metrics.values()))
    volatility_rows: list[tuple[Any, ...]] = []
    for key, aggregate in volatility_grid.items():
        metrics = aggregate.metrics(include_clusters=False)
        volatility_rows.append((*key, *metrics.values()))
    connection.executemany(
        "INSERT INTO grid_statistics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        grid_rows,
    )
    connection.executemany(
        "INSERT INTO volatility_statistics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        volatility_rows,
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key,value_json) VALUES (?,?)",
        ("analysis_completed_utc", json.dumps(utc_now())),
    )
    connection.commit()

    threshold_summary_rows = connection.execute(
        """
        SELECT symbol,detector_threshold_bp,COUNT(*),
               AVG(retracement_bp),AVG(rv60_bp),AVG(prior_leg_return_bp),
               AVG(extreme_age_bars)
        FROM candidates
        GROUP BY symbol,detector_threshold_bp
        ORDER BY symbol,detector_threshold_bp
        """
    ).fetchall()
    threshold_summary = [
        {
            "symbol": row[0],
            "detector_threshold_bp": row[1],
            "candidates": row[2],
            "mean_actual_retracement_bp": row[3],
            "mean_rv60_bp": row[4],
            "mean_prior_leg_return_bp": row[5],
            "mean_extreme_age_bars": row[6],
        }
        for row in threshold_summary_rows
    ]
    ranked = []
    for key, aggregate in grid.items():
        metrics = aggregate.metrics()
        if metrics["observations"] >= 1_000:
            ranked.append(
                {
                    "symbol": key[0],
                    "detector_threshold_bp": key[1],
                    "stop_bp": key[2],
                    "reward_risk": key[3],
                    "horizon_minutes": key[4],
                    **metrics,
                }
            )
    ranked.sort(
        key=lambda item: (
            item["weekly_cluster_lower_95_bp"]
            if item["weekly_cluster_lower_95_bp"] is not None
            else -math.inf
        ),
        reverse=True,
    )
    report = {
        "engine_version": ENGINE_VERSION,
        "analysis_completed_utc": utc_now(),
        "database": database_path.name,
        "candidate_count": processed,
        "cost_assumptions_bp": {
            "round_trip_taker_fees": fee_bp,
            "entry_slippage_already_embedded_in_entry_price": config["execution"][
                "entry_slippage_bp"
            ],
            "exit_slippage": exit_slippage_bp,
            "funding": "historical rate applied when an exit crosses a funding timestamp",
        },
        "threshold_summary": threshold_summary,
        "top_discovery_configurations_by_weekly_cluster_lower_95": ranked[:50],
        "warning": (
            "Discovery rankings are hypotheses only. They cannot be called an edge "
            "until 2024 validation and the locked 2025-2026 test pass."
        ),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    connection.close()
    return report


def self_test() -> None:
    barriers = np.asarray([10.0, 20.0, 30.0])
    cumulative = np.asarray([4.0, 12.0, 12.0, 35.0])
    assert first_passage_minutes(cumulative, barriers).tolist() == [2, 4, 4]
    aggregate = Aggregate()
    aggregate.add("win", 10.0, 3, False)
    aggregate.add("loss", -5.0, 2, True)
    metrics = aggregate.metrics()
    assert metrics["observations"] == 2
    assert math.isclose(metrics["mean_net_return_bp"], 2.5)
    assert math.isclose(funding_net_bp(1, 5, [3, 8], [0.001, 0.002]), -10.0)
    assert math.isclose(funding_net_bp(-1, 5, [3, 8], [0.001, 0.002]), 10.0)
    print("self-test: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("self-test")

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--data-dir", type=Path, required=True)
    collect_parser.add_argument("--config", type=Path, required=True)
    collect_parser.add_argument("--database", type=Path, required=True)
    collect_parser.add_argument("--symbols", nargs="+")
    collect_parser.add_argument("--start")
    collect_parser.add_argument("--end")
    collect_parser.add_argument("--summary", type=Path)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--database", type=Path, required=True)
    analyze_parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "self-test":
        self_test()
        return 0
    if args.command == "collect":
        result = collect(
            args.data_dir,
            args.config,
            args.database,
            symbols=args.symbols,
            start=args.start,
            end=args.end,
        )
        if args.summary:
            args.summary.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "analyze":
        result = analyze(args.database, args.report)
        print(
            json.dumps(
                {
                    "candidate_count": result["candidate_count"],
                    "top_configuration": result[
                        "top_discovery_configurations_by_weekly_cluster_lower_95"
                    ][:1],
                    "warning": result["warning"],
                },
                indent=2,
            )
        )
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
