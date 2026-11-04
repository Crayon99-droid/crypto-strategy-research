#!/usr/bin/env python3
"""Phase 2 causal probability model for dynamic directional-change thresholds.

The script derives a compact modeling table from the verified Phase 1 event
store, then evaluates calibrated probability models using chronological
walk-forward folds entirely inside the 2021-2023 discovery period.

This is a research engine, not a live trading system.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ENGINE_VERSION = "1.0.0"
CLASS_NAMES = {0: "sl_first", 1: "timeout", 2: "tp_first"}
MILLISECONDS_PER_MINUTE = 60_000
MILLISECONDS_PER_WEEK = 7 * 24 * 60 * 60 * 1_000

RAW_FEATURE_COLUMNS = [
    "retracement_bp",
    "prior_leg_return_bp",
    "prior_leg_bars",
    "extreme_age_bars",
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
    "minute_of_day_utc",
    "day_of_week_utc",
]


def utc_now() -> str:
    return pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_json_array(value: str) -> list[Any]:
    return json.loads(value)


def funding_adjustment_bp(
    side: int,
    exit_minute: int,
    funding_minutes: Sequence[int],
    funding_rates: Sequence[float],
) -> float:
    crossed_rate = sum(
        float(rate)
        for minute, rate in zip(funding_minutes, funding_rates)
        if int(minute) <= exit_minute
    )
    return -side * crossed_rate * 10_000.0


@dataclass(frozen=True)
class DerivedOutcome:
    outcome_class: int
    net_return_bp: float
    exit_minutes: int
    target_actual_bp: float
    stop_actual_bp: float
    ambiguous_same_minute: int


def derive_outcome(
    threshold_bp: int,
    side: int,
    barrier_index: dict[float, int],
    horizon_index: int,
    favorable_minutes: Sequence[int],
    adverse_minutes: Sequence[int],
    favorable_barriers: Sequence[float],
    adverse_barriers: Sequence[float],
    horizon_close_returns: Sequence[float],
    funding_minutes: Sequence[int],
    funding_rates: Sequence[float],
    fee_bp: float,
    exit_slippage_bp: float,
    horizon_minutes: int,
) -> DerivedOutcome:
    index = barrier_index[round(float(threshold_bp), 8)]
    favorable_minute = int(favorable_minutes[index])
    adverse_minute = int(adverse_minutes[index])
    target_actual = float(favorable_barriers[index])
    stop_actual = float(adverse_barriers[index])
    hit_favorable = 0 < favorable_minute <= horizon_minutes
    hit_adverse = 0 < adverse_minute <= horizon_minutes
    ambiguous = int(
        hit_favorable and hit_adverse and favorable_minute == adverse_minute
    )

    if hit_adverse and (not hit_favorable or adverse_minute <= favorable_minute):
        outcome_class = 0
        gross_return = -stop_actual
        exit_minutes = adverse_minute
    elif hit_favorable:
        outcome_class = 2
        gross_return = target_actual
        exit_minutes = favorable_minute
    else:
        outcome_class = 1
        gross_return = float(horizon_close_returns[horizon_index])
        exit_minutes = horizon_minutes

    net_return = (
        gross_return
        - fee_bp
        - exit_slippage_bp
        + funding_adjustment_bp(
            side, exit_minutes, funding_minutes, funding_rates
        )
    )
    return DerivedOutcome(
        outcome_class=outcome_class,
        net_return_bp=net_return,
        exit_minutes=exit_minutes,
        target_actual_bp=target_actual,
        stop_actual_bp=stop_actual,
        ambiguous_same_minute=ambiguous,
    )


def create_samples_database(
    source_database: Path,
    config_path: Path,
    output_database: Path,
    summary_path: Path,
) -> dict[str, Any]:
    config = load_json(config_path)
    if output_database.exists():
        output_database.unlink()

    source = sqlite3.connect(source_database)
    source.row_factory = sqlite3.Row
    metadata = {
        key: json.loads(value)
        for key, value in source.execute("SELECT key,value_json FROM metadata")
    }
    if metadata["end_exclusive"] > config["discovery_end_exclusive"]:
        raise ValueError("source database crosses the Phase 2 discovery boundary")

    barriers = [float(value) for value in metadata["barrier_levels_bp"]]
    barrier_index = {round(value, 8): index for index, value in enumerate(barriers)}
    thresholds = metadata["config"]["detector_thresholds_bp"]
    missing = [value for value in thresholds if round(float(value), 8) not in barrier_index]
    if missing:
        raise ValueError(f"Phase 1 store lacks detector barriers: {missing}")

    horizon_minutes = int(config["label"]["maximum_holding_minutes"])
    horizons = metadata["config"]["outcome_horizons_minutes"]
    if horizon_minutes not in horizons:
        raise ValueError("requested label horizon is absent from Phase 1")
    horizon_index = horizons.index(horizon_minutes)
    fee_bp = float(config["execution"]["round_trip_taker_fees_bp"])
    exit_slippage_bp = float(config["execution"]["exit_slippage_bp"])

    target = sqlite3.connect(output_database)
    target.execute("PRAGMA journal_mode=OFF")
    target.execute("PRAGMA synchronous=OFF")
    target.execute("PRAGMA temp_store=MEMORY")
    target.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
    target.execute(
        """
        CREATE TABLE samples (
            candidate_id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            threshold_bp INTEGER NOT NULL,
            side INTEGER NOT NULL,
            market_episode_id TEXT NOT NULL,
            entry_timestamp_ms INTEGER NOT NULL,
            outcome_class INTEGER NOT NULL,
            net_return_bp REAL NOT NULL,
            exit_minutes INTEGER NOT NULL,
            target_actual_bp REAL NOT NULL,
            stop_actual_bp REAL NOT NULL,
            ambiguous_same_minute INTEGER NOT NULL,
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
            day_of_week_utc INTEGER NOT NULL
        )
        """
    )

    query = """
        SELECT candidate_id,symbol,detector_threshold_bp,side,market_episode_id,
               entry_timestamp_ms,available_minutes,
               favorable_first_minutes_json,adverse_first_minutes_json,
               favorable_barriers_actual_bp_json,adverse_barriers_actual_bp_json,
               close_return_horizons_bp_json,funding_minutes_json,
               funding_rates_json,retracement_bp,prior_leg_return_bp,
               prior_leg_bars,extreme_age_bars,rv15_bp,rv60_bp,rv240_bp,
               atr14_bp,atr60_bp,return1_bp,return5_bp,return15_bp,
               return60_bp,return240_bp,volume_z20,volume_z60,
               sma60_distance_bp,sma240_distance_bp,sma60_slope15_bp,
               bar_range_bp,close_location,latest_funding_rate,
               minute_of_day_utc,day_of_week_utc
        FROM candidates
        WHERE available_minutes >= ?
          AND entry_timestamp_ms < ?
        ORDER BY candidate_id
    """
    discovery_end_ms = int(pd.Timestamp(config["discovery_end_exclusive"]).timestamp() * 1000)
    cursor = source.execute(query, (horizon_minutes, discovery_end_ms))
    insert = "INSERT INTO samples VALUES (" + ",".join("?" for _ in range(36)) + ")"
    batch: list[tuple[Any, ...]] = []
    counts: dict[str, Any] = {
        "total": 0,
        "by_symbol": defaultdict(int),
        "by_outcome": defaultdict(int),
        "ambiguous_same_minute": 0,
    }
    while True:
        rows = cursor.fetchmany(5_000)
        if not rows:
            break
        for row in rows:
            outcome = derive_outcome(
                threshold_bp=int(row["detector_threshold_bp"]),
                side=int(row["side"]),
                barrier_index=barrier_index,
                horizon_index=horizon_index,
                favorable_minutes=parse_json_array(row["favorable_first_minutes_json"]),
                adverse_minutes=parse_json_array(row["adverse_first_minutes_json"]),
                favorable_barriers=parse_json_array(
                    row["favorable_barriers_actual_bp_json"]
                ),
                adverse_barriers=parse_json_array(
                    row["adverse_barriers_actual_bp_json"]
                ),
                horizon_close_returns=parse_json_array(
                    row["close_return_horizons_bp_json"]
                ),
                funding_minutes=parse_json_array(row["funding_minutes_json"]),
                funding_rates=parse_json_array(row["funding_rates_json"]),
                fee_bp=fee_bp,
                exit_slippage_bp=exit_slippage_bp,
                horizon_minutes=horizon_minutes,
            )
            batch.append(
                (
                    int(row["candidate_id"]),
                    row["symbol"],
                    int(row["detector_threshold_bp"]),
                    int(row["side"]),
                    row["market_episode_id"],
                    int(row["entry_timestamp_ms"]),
                    outcome.outcome_class,
                    outcome.net_return_bp,
                    outcome.exit_minutes,
                    outcome.target_actual_bp,
                    outcome.stop_actual_bp,
                    outcome.ambiguous_same_minute,
                    *[row[name] for name in RAW_FEATURE_COLUMNS],
                )
            )
            counts["total"] += 1
            counts["by_symbol"][row["symbol"]] += 1
            counts["by_outcome"][CLASS_NAMES[outcome.outcome_class]] += 1
            counts["ambiguous_same_minute"] += outcome.ambiguous_same_minute
        if len(batch) >= 5_000:
            target.executemany(insert, batch)
            target.commit()
            batch.clear()
            if counts["total"] % 50_000 == 0:
                print(f"prepared {counts['total']:,} samples", flush=True)
    if batch:
        target.executemany(insert, batch)
        target.commit()

    target.execute("CREATE INDEX idx_samples_time ON samples(entry_timestamp_ms)")
    target.execute("CREATE INDEX idx_samples_episode ON samples(market_episode_id)")
    target.execute("CREATE INDEX idx_samples_threshold ON samples(symbol,threshold_bp)")
    run_metadata = {
        "engine_version": ENGINE_VERSION,
        "created_utc": utc_now(),
        "source_database": source_database.name,
        "source_database_sha256": sha256(source_database),
        "config": config,
        "config_sha256": sha256(config_path),
        "barrier_levels_bp": barriers,
        "class_names": CLASS_NAMES,
    }
    for key, value in run_metadata.items():
        target.execute(
            "INSERT INTO metadata(key,value_json) VALUES (?,?)",
            (key, json.dumps(value, separators=(",", ":"), allow_nan=False)),
        )
    target.commit()
    integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
    source.close()
    target.close()
    if integrity != "ok":
        raise RuntimeError(f"samples database integrity failed: {integrity}")

    summary = {
        "engine_version": ENGINE_VERSION,
        "created_utc": utc_now(),
        "source_database": source_database.name,
        "output_database": output_database.name,
        "integrity": integrity,
        "label": config["label"],
        "samples": counts["total"],
        "samples_by_symbol": dict(counts["by_symbol"]),
        "samples_by_outcome": dict(counts["by_outcome"]),
        "ambiguous_same_minute": counts["ambiguous_same_minute"],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.where(denominator.abs() > 1e-12, np.nan)


def build_feature_frame(data: pd.DataFrame) -> pd.DataFrame:
    threshold = data["threshold_bp"].astype(float)
    side = data["side"].astype(float)
    features = pd.DataFrame(index=data.index)
    features["symbol_is_xrp"] = (data["symbol"] == "XRPUSDT").astype(float)
    features["side"] = side
    features["threshold_bp"] = threshold
    features["log_threshold"] = np.log(threshold)

    for name in RAW_FEATURE_COLUMNS:
        if name not in ("minute_of_day_utc", "day_of_week_utc"):
            features[name] = pd.to_numeric(data[name], errors="coerce")

    features["retracement_over_threshold"] = safe_ratio(
        data["retracement_bp"].astype(float), threshold
    )
    features["prior_leg_over_threshold"] = safe_ratio(
        data["prior_leg_return_bp"].astype(float), threshold
    )
    features["log_prior_leg_bars"] = np.log1p(data["prior_leg_bars"].astype(float))
    features["log_extreme_age_bars"] = np.log1p(
        data["extreme_age_bars"].astype(float)
    )
    features["prior_leg_speed_bp"] = safe_ratio(
        data["prior_leg_return_bp"].astype(float),
        data["prior_leg_bars"].astype(float) + 1.0,
    )
    for name in ("rv15_bp", "rv60_bp", "rv240_bp", "atr14_bp", "atr60_bp"):
        features[f"threshold_over_{name}"] = safe_ratio(
            threshold, data[name].astype(float)
        )
    features["rv15_over_rv60"] = safe_ratio(
        data["rv15_bp"].astype(float), data["rv60_bp"].astype(float)
    )
    features["rv60_over_rv240"] = safe_ratio(
        data["rv60_bp"].astype(float), data["rv240_bp"].astype(float)
    )

    for name in ("return1_bp", "return5_bp", "return15_bp", "return60_bp", "return240_bp"):
        features[f"aligned_{name}"] = side * data[name].astype(float)
    for name in ("sma60_distance_bp", "sma240_distance_bp", "sma60_slope15_bp"):
        features[f"aligned_{name}"] = side * data[name].astype(float)
    features["aligned_funding_cost_bp"] = (
        side * data["latest_funding_rate"].astype(float) * 10_000.0
    )

    minute_angle = 2.0 * math.pi * data["minute_of_day_utc"].astype(float) / 1_440.0
    day_angle = 2.0 * math.pi * data["day_of_week_utc"].astype(float) / 7.0
    features["minute_sin"] = np.sin(minute_angle)
    features["minute_cos"] = np.cos(minute_angle)
    features["day_sin"] = np.sin(day_angle)
    features["day_cos"] = np.cos(day_angle)
    return features.astype(np.float32)


def episode_balanced_weights(data: pd.DataFrame) -> np.ndarray:
    counts = data.groupby("market_episode_id", sort=False)["candidate_id"].transform("size")
    weights = 1.0 / counts.to_numpy(dtype=float)
    return weights / weights.mean()


def make_estimator(name: str, config: dict[str, Any]) -> Pipeline:
    if name == "logistic":
        settings = config["models"]["logistic"]
        classifier = LogisticRegression(
            C=float(settings["regularization_c"]),
            max_iter=int(settings["max_iterations"]),
            solver="lbfgs",
            random_state=int(config["statistics"]["random_seed"]),
        )
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("classifier", classifier),
            ]
        )
    if name == "hist_gradient_boosting":
        settings = config["models"]["hist_gradient_boosting"]
        classifier = HistGradientBoostingClassifier(
            learning_rate=float(settings["learning_rate"]),
            max_iter=int(settings["max_iterations"]),
            max_leaf_nodes=int(settings["max_leaf_nodes"]),
            min_samples_leaf=int(settings["minimum_samples_leaf"]),
            l2_regularization=float(settings["l2_regularization"]),
            random_state=int(config["statistics"]["random_seed"]),
        )
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("classifier", classifier),
            ]
        )
    raise ValueError(f"unknown model: {name}")


def fit_estimator(
    estimator: Pipeline,
    features: pd.DataFrame,
    labels: np.ndarray,
    weights: np.ndarray,
) -> Pipeline:
    estimator.fit(
        features,
        labels,
        classifier__sample_weight=weights,
    )
    return estimator


def aligned_probabilities(estimator: Any, features: pd.DataFrame) -> np.ndarray:
    raw = estimator.predict_proba(features)
    output = np.zeros((len(features), 3), dtype=float)
    for source_index, class_value in enumerate(estimator.classes_):
        output[:, int(class_value)] = raw[:, source_index]
    return output


def fit_calibrator(
    base_probabilities: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    seed: int,
) -> LogisticRegression:
    log_probability = np.log(np.clip(base_probabilities, 1e-7, 1.0))
    calibrator = LogisticRegression(
        C=100.0,
        max_iter=300,
        solver="lbfgs",
        random_state=seed,
    )
    calibrator.fit(log_probability, labels, sample_weight=weights)
    return calibrator


def calibrated_probabilities(
    base_estimator: Pipeline,
    calibrator: LogisticRegression,
    features: pd.DataFrame,
) -> np.ndarray:
    base = aligned_probabilities(base_estimator, features)
    raw = calibrator.predict_proba(np.log(np.clip(base, 1e-7, 1.0)))
    output = np.zeros((len(features), 3), dtype=float)
    for source_index, class_value in enumerate(calibrator.classes_):
        output[:, int(class_value)] = raw[:, source_index]
    return output


def payoff_lookup(
    calibration: pd.DataFrame,
    minimum_group_observations: int,
) -> tuple[dict[tuple[Any, ...], float], dict[tuple[Any, ...], int]]:
    levels: dict[tuple[Any, ...], float] = {}
    counts: dict[tuple[Any, ...], int] = {}

    def add_level(group_columns: list[str], prefix: str) -> None:
        grouped = calibration.groupby(group_columns, observed=True)["net_return_bp"].agg(
            ["mean", "count"]
        )
        for keys, row in grouped.iterrows():
            if not isinstance(keys, tuple):
                keys = (keys,)
            key = (prefix, *keys)
            levels[key] = float(row["mean"])
            counts[key] = int(row["count"])

    add_level(["symbol", "threshold_bp", "outcome_class"], "symbol_threshold")
    add_level(["symbol", "outcome_class"], "symbol")
    add_level(["outcome_class"], "global")
    counts[("minimum",)] = minimum_group_observations
    return levels, counts


def payoff_matrix(
    data: pd.DataFrame,
    levels: dict[tuple[Any, ...], float],
    counts: dict[tuple[Any, ...], int],
) -> np.ndarray:
    minimum = counts[("minimum",)]
    output = np.zeros((len(data), 3), dtype=float)
    symbols = data["symbol"].to_numpy()
    thresholds = data["threshold_bp"].to_numpy(dtype=int)
    for row_index, (symbol, threshold) in enumerate(zip(symbols, thresholds)):
        for class_value in range(3):
            detailed = ("symbol_threshold", symbol, threshold, class_value)
            symbol_key = ("symbol", symbol, class_value)
            global_key = ("global", class_value)
            if counts.get(detailed, 0) >= minimum:
                value = levels[detailed]
            elif counts.get(symbol_key, 0) >= minimum:
                value = levels[symbol_key]
            else:
                value = levels[global_key]
            output[row_index, class_value] = value
    return output


def simulate_policy(
    data: pd.DataFrame,
    probabilities: np.ndarray,
    predicted_ev: np.ndarray,
    model_name: str,
    fold_name: str,
    hurdle_bp: float,
) -> list[dict[str, Any]]:
    scored = data[
        [
            "candidate_id",
            "symbol",
            "threshold_bp",
            "side",
            "entry_timestamp_ms",
            "outcome_class",
            "net_return_bp",
            "exit_minutes",
        ]
    ].copy()
    scored["predicted_ev_bp"] = predicted_ev
    scored["prob_sl"] = probabilities[:, 0]
    scored["prob_timeout"] = probabilities[:, 1]
    scored["prob_tp"] = probabilities[:, 2]
    scored = scored[scored["predicted_ev_bp"] >= hurdle_bp]
    trades: list[dict[str, Any]] = []
    for symbol, symbol_rows in scored.groupby("symbol", sort=False):
        active_until = -1
        ordered = symbol_rows.sort_values(
            ["entry_timestamp_ms", "predicted_ev_bp", "threshold_bp"],
            ascending=[True, False, False],
            kind="stable",
        )
        for _, timestamp_rows in ordered.groupby("entry_timestamp_ms", sort=True):
            row = timestamp_rows.iloc[0]
            entry_timestamp = int(row["entry_timestamp_ms"])
            if entry_timestamp < active_until:
                continue
            exit_timestamp = entry_timestamp + int(row["exit_minutes"]) * MILLISECONDS_PER_MINUTE
            active_until = exit_timestamp
            trades.append(
                {
                    "model": model_name,
                    "fold": fold_name,
                    "candidate_id": int(row["candidate_id"]),
                    "symbol": symbol,
                    "threshold_bp": int(row["threshold_bp"]),
                    "side": int(row["side"]),
                    "entry_timestamp_ms": entry_timestamp,
                    "entry_timestamp_utc": pd.to_datetime(
                        entry_timestamp, unit="ms", utc=True
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "exit_timestamp_ms": exit_timestamp,
                    "exit_minutes": int(row["exit_minutes"]),
                    "predicted_ev_bp": float(row["predicted_ev_bp"]),
                    "prob_sl": float(row["prob_sl"]),
                    "prob_timeout": float(row["prob_timeout"]),
                    "prob_tp": float(row["prob_tp"]),
                    "outcome_class": int(row["outcome_class"]),
                    "outcome": CLASS_NAMES[int(row["outcome_class"])],
                    "net_return_bp": float(row["net_return_bp"]),
                }
            )
    return sorted(trades, key=lambda row: (row["entry_timestamp_ms"], row["symbol"]))


def simulate_fixed_threshold(
    data: pd.DataFrame,
    threshold_bp: int,
    fold_name: str,
) -> list[dict[str, Any]]:
    subset = data[data["threshold_bp"] == threshold_bp]
    trades: list[dict[str, Any]] = []
    for symbol, symbol_rows in subset.groupby("symbol", sort=False):
        active_until = -1
        for row in symbol_rows.sort_values("entry_timestamp_ms", kind="stable").itertuples():
            entry_timestamp = int(row.entry_timestamp_ms)
            if entry_timestamp < active_until:
                continue
            exit_timestamp = entry_timestamp + int(row.exit_minutes) * MILLISECONDS_PER_MINUTE
            active_until = exit_timestamp
            trades.append(
                {
                    "model": f"fixed_{threshold_bp}",
                    "fold": fold_name,
                    "candidate_id": int(row.candidate_id),
                    "symbol": symbol,
                    "threshold_bp": threshold_bp,
                    "side": int(row.side),
                    "entry_timestamp_ms": entry_timestamp,
                    "exit_timestamp_ms": exit_timestamp,
                    "exit_minutes": int(row.exit_minutes),
                    "outcome_class": int(row.outcome_class),
                    "net_return_bp": float(row.net_return_bp),
                }
            )
    return sorted(trades, key=lambda row: (row["entry_timestamp_ms"], row["symbol"]))


def maximum_drawdown(values: np.ndarray) -> float | None:
    if len(values) == 0:
        return None
    equity = np.cumsum(values)
    peaks = np.maximum.accumulate(np.r_[0.0, equity])[:-1]
    return float(np.max(peaks - equity))


def performance_statistics(
    trades: Sequence[dict[str, Any]],
    bootstrap_trials: int,
    seed: int,
) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "net_positive_rate": None,
            "mean_net_return_bp": None,
            "median_net_return_bp": None,
            "total_net_return_bp": 0.0,
            "profit_factor": None,
            "maximum_drawdown_bp": None,
            "average_exit_minutes": None,
            "weekly_clusters": 0,
            "weekly_cluster_standard_error_bp": None,
            "weekly_cluster_lower_95_bp": None,
            "bootstrap_mean_lower_95_bp": None,
            "bootstrap_mean_upper_95_bp": None,
        }
    ordered = sorted(trades, key=lambda row: (row["entry_timestamp_ms"], row["symbol"]))
    returns = np.asarray([float(row["net_return_bp"]) for row in ordered])
    weeks = np.asarray(
        [int(row["entry_timestamp_ms"]) // MILLISECONDS_PER_WEEK for row in ordered]
    )
    mean = float(returns.mean())
    unique_weeks = np.unique(weeks)
    residual_sums = []
    weekly_counts = []
    weekly_sums = []
    for week in unique_weeks:
        mask = weeks == week
        count = int(mask.sum())
        value_sum = float(returns[mask].sum())
        residual_sums.append(value_sum - count * mean)
        weekly_counts.append(count)
        weekly_sums.append(value_sum)
    if len(unique_weeks) > 1:
        cluster_se = (
            math.sqrt(
                len(unique_weeks)
                / (len(unique_weeks) - 1)
                * sum(value * value for value in residual_sums)
            )
            / len(returns)
        )
        cluster_lower = mean - 1.96 * cluster_se
    else:
        cluster_se = None
        cluster_lower = None

    rng = np.random.default_rng(seed)
    count_array = np.asarray(weekly_counts, dtype=float)
    sum_array = np.asarray(weekly_sums, dtype=float)
    bootstrap_means = np.empty(bootstrap_trials, dtype=float)
    for trial in range(bootstrap_trials):
        chosen = rng.integers(0, len(unique_weeks), len(unique_weeks))
        denominator = count_array[chosen].sum()
        bootstrap_means[trial] = (
            sum_array[chosen].sum() / denominator if denominator > 0 else np.nan
        )
    bootstrap_means = bootstrap_means[np.isfinite(bootstrap_means)]
    gross_positive = float(returns[returns > 0.0].sum())
    gross_negative = float(-returns[returns < 0.0].sum())
    return {
        "trades": int(len(returns)),
        "net_positive_rate": float((returns > 0.0).mean()),
        "mean_net_return_bp": mean,
        "median_net_return_bp": float(np.median(returns)),
        "total_net_return_bp": float(returns.sum()),
        "profit_factor": gross_positive / gross_negative if gross_negative > 0 else None,
        "maximum_drawdown_bp": maximum_drawdown(returns),
        "average_exit_minutes": float(
            np.mean([float(row["exit_minutes"]) for row in ordered])
        ),
        "weekly_clusters": int(len(unique_weeks)),
        "weekly_cluster_standard_error_bp": cluster_se,
        "weekly_cluster_lower_95_bp": cluster_lower,
        "bootstrap_mean_lower_95_bp": float(np.quantile(bootstrap_means, 0.025)),
        "bootstrap_mean_upper_95_bp": float(np.quantile(bootstrap_means, 0.975)),
    }


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    opener: Any = gzip.open if path.suffix == ".gz" else open
    mode = "wt" if path.suffix == ".gz" else "w"
    with opener(path, mode, encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def walk_forward(
    samples_database: Path,
    config_path: Path,
    output_directory: Path,
) -> dict[str, Any]:
    config = load_json(config_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(samples_database)
    data = pd.read_sql_query(
        "SELECT * FROM samples ORDER BY entry_timestamp_ms,candidate_id",
        connection,
    )
    connection.close()
    discovery_end_ms = int(pd.Timestamp(config["discovery_end_exclusive"]).timestamp() * 1000)
    if int(data["entry_timestamp_ms"].max()) >= discovery_end_ms:
        raise ValueError("Phase 2 samples contain validation or test data")
    features = build_feature_frame(data)
    feature_names = list(features.columns)
    hurdle = float(config["policy"]["minimum_predicted_expected_net_bp"])
    minimum_lookup = int(config["policy"]["payoff_lookup_minimum_group_observations"])
    bootstrap_trials = int(config["statistics"]["weekly_block_bootstrap_trials"])
    seed = int(config["statistics"]["random_seed"])
    calibration_days = int(config["calibration_lookback_days"])

    fold_rows: list[dict[str, Any]] = []
    dynamic_trades: list[dict[str, Any]] = []
    sweep_trades: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    policy_sweep_rows: list[dict[str, Any]] = []
    score_bin_rows: list[dict[str, Any]] = []
    fixed_trades: dict[int, list[dict[str, Any]]] = defaultdict(list)
    coefficient_rows: list[dict[str, Any]] = []

    for fold_number, fold in enumerate(config["walk_forward_folds"]):
        fold_name = fold["name"]
        test_start = pd.Timestamp(fold["test_start"])
        test_end = pd.Timestamp(fold["test_end_exclusive"])
        calibration_start = test_start - pd.Timedelta(days=calibration_days)
        test_start_ms = int(test_start.timestamp() * 1000)
        test_end_ms = int(test_end.timestamp() * 1000)
        calibration_start_ms = int(calibration_start.timestamp() * 1000)
        base_mask = data["entry_timestamp_ms"] < calibration_start_ms
        calibration_mask = (
            (data["entry_timestamp_ms"] >= calibration_start_ms)
            & (data["entry_timestamp_ms"] < test_start_ms)
        )
        test_mask = (
            (data["entry_timestamp_ms"] >= test_start_ms)
            & (data["entry_timestamp_ms"] < test_end_ms)
        )
        base_data = data.loc[base_mask]
        calibration_data = data.loc[calibration_mask]
        test_data = data.loc[test_mask]
        if min(len(base_data), len(calibration_data), len(test_data)) == 0:
            raise RuntimeError(f"empty split in {fold_name}")
        base_weights = episode_balanced_weights(base_data)
        calibration_weights = episode_balanced_weights(calibration_data)
        payoffs, payoff_counts = payoff_lookup(calibration_data, minimum_lookup)
        test_payoffs = payoff_matrix(test_data, payoffs, payoff_counts)

        print(
            f"[{fold_name}] base={len(base_data):,} calibration={len(calibration_data):,} "
            f"test={len(test_data):,}",
            flush=True,
        )
        for model_index, model_name in enumerate(config["models"]):
            began = time.monotonic()
            estimator = make_estimator(model_name, config)
            fit_estimator(
                estimator,
                features.loc[base_mask],
                base_data["outcome_class"].to_numpy(dtype=int),
                base_weights,
            )
            calibration_base_probability = aligned_probabilities(
                estimator, features.loc[calibration_mask]
            )
            calibrator = fit_calibrator(
                calibration_base_probability,
                calibration_data["outcome_class"].to_numpy(dtype=int),
                calibration_weights,
                seed + fold_number * 10 + model_index,
            )
            test_probability = calibrated_probabilities(
                estimator, calibrator, features.loc[test_mask]
            )
            predicted_ev = np.sum(test_probability * test_payoffs, axis=1)
            model_trades: list[dict[str, Any]] = []
            for diagnostic_hurdle in config["policy"][
                "diagnostic_expected_value_hurdles_bp"
            ]:
                diagnostic_hurdle = float(diagnostic_hurdle)
                diagnostic_trades = simulate_policy(
                    test_data,
                    test_probability,
                    predicted_ev,
                    model_name,
                    fold_name,
                    diagnostic_hurdle,
                )
                sweep_trades[(model_name, diagnostic_hurdle)].extend(
                    diagnostic_trades
                )
                diagnostic_metrics = performance_statistics(
                    diagnostic_trades,
                    bootstrap_trials,
                    seed
                    + 50_000
                    + fold_number * 100
                    + model_index * 10
                    + int(diagnostic_hurdle),
                )
                policy_sweep_rows.append(
                    {
                        "fold": fold_name,
                        "model": model_name,
                        "predicted_ev_hurdle_bp": diagnostic_hurdle,
                        "eligible_predictions": int(
                            (predicted_ev >= diagnostic_hurdle).sum()
                        ),
                        **diagnostic_metrics,
                    }
                )
                if math.isclose(diagnostic_hurdle, hurdle):
                    model_trades = diagnostic_trades
            if not model_trades and hurdle not in {
                float(item)
                for item in config["policy"]["diagnostic_expected_value_hurdles_bp"]
            }:
                model_trades = simulate_policy(
                    test_data,
                    test_probability,
                    predicted_ev,
                    model_name,
                    fold_name,
                    hurdle,
                )

            score_frame = pd.DataFrame(
                {
                    "predicted_ev_bp": predicted_ev,
                    "actual_net_return_bp": test_data["net_return_bp"].to_numpy(
                        dtype=float
                    ),
                    "outcome_class": test_data["outcome_class"].to_numpy(dtype=int),
                }
            )
            score_frame["score_decile"] = pd.qcut(
                score_frame["predicted_ev_bp"],
                10,
                labels=False,
                duplicates="drop",
            )
            for score_decile, score_rows in score_frame.groupby(
                "score_decile", observed=True
            ):
                score_bin_rows.append(
                    {
                        "fold": fold_name,
                        "model": model_name,
                        "score_decile": int(score_decile),
                        "observations": len(score_rows),
                        "mean_predicted_ev_bp": float(
                            score_rows["predicted_ev_bp"].mean()
                        ),
                        "mean_actual_net_return_bp": float(
                            score_rows["actual_net_return_bp"].mean()
                        ),
                        "net_positive_rate": float(
                            (score_rows["actual_net_return_bp"] > 0.0).mean()
                        ),
                        "tp_first_rate": float(
                            (score_rows["outcome_class"] == 2).mean()
                        ),
                        "sl_first_rate": float(
                            (score_rows["outcome_class"] == 0).mean()
                        ),
                        "timeout_rate": float(
                            (score_rows["outcome_class"] == 1).mean()
                        ),
                    }
                )
            dynamic_trades.extend(model_trades)
            classification_metrics = {
                "log_loss": float(
                    log_loss(
                        test_data["outcome_class"].to_numpy(dtype=int),
                        test_probability,
                        labels=[0, 1, 2],
                    )
                ),
                "accuracy": float(
                    accuracy_score(
                        test_data["outcome_class"].to_numpy(dtype=int),
                        np.argmax(test_probability, axis=1),
                    )
                ),
            }
            trade_metrics = performance_statistics(
                model_trades,
                bootstrap_trials,
                seed + fold_number * 100 + model_index,
            )
            fold_rows.append(
                {
                    "fold": fold_name,
                    "model": model_name,
                    "base_samples": len(base_data),
                    "calibration_samples": len(calibration_data),
                    "test_samples": len(test_data),
                    "eligible_predictions": int((predicted_ev >= hurdle).sum()),
                    "mean_predicted_ev_bp": float(predicted_ev.mean()),
                    **classification_metrics,
                    **trade_metrics,
                    "elapsed_seconds": time.monotonic() - began,
                }
            )
            if model_name == "logistic":
                classifier = estimator.named_steps["classifier"]
                for class_index, class_value in enumerate(classifier.classes_):
                    for feature_name, coefficient in zip(
                        feature_names, classifier.coef_[class_index]
                    ):
                        coefficient_rows.append(
                            {
                                "fold": fold_name,
                                "class": CLASS_NAMES[int(class_value)],
                                "feature": feature_name,
                                "standardized_coefficient": float(coefficient),
                            }
                        )
            print(
                f"[{fold_name}] {model_name}: {len(model_trades):,} trades, "
                f"mean={trade_metrics.get('mean_net_return_bp')}",
                flush=True,
            )

        for threshold in sorted(data["threshold_bp"].unique()):
            fixed_trades[int(threshold)].extend(
                simulate_fixed_threshold(test_data, int(threshold), fold_name)
            )

    aggregate_models: list[dict[str, Any]] = []
    for model_index, model_name in enumerate(config["models"]):
        trades = [row for row in dynamic_trades if row["model"] == model_name]
        thresholds = defaultdict(int)
        for row in trades:
            thresholds[int(row["threshold_bp"])] += 1
        aggregate_models.append(
            {
                "model": model_name,
                **performance_statistics(
                    trades, bootstrap_trials, seed + 10_000 + model_index
                ),
                "threshold_selection_counts": dict(sorted(thresholds.items())),
            }
        )

    baseline_rows: list[dict[str, Any]] = []
    for index, threshold in enumerate(sorted(fixed_trades)):
        baseline_rows.append(
            {
                "threshold_bp": threshold,
                **performance_statistics(
                    fixed_trades[threshold],
                    bootstrap_trials,
                    seed + 20_000 + index,
                ),
            }
        )

    aggregate_policy_sweep: list[dict[str, Any]] = []
    all_sweep_trade_rows: list[dict[str, Any]] = []
    for index, ((model_name, diagnostic_hurdle), trades) in enumerate(
        sorted(sweep_trades.items())
    ):
        threshold_counts: dict[int, int] = defaultdict(int)
        symbol_counts: dict[str, int] = defaultdict(int)
        for trade in trades:
            threshold_counts[int(trade["threshold_bp"])] += 1
            symbol_counts[str(trade["symbol"])] += 1
            all_sweep_trade_rows.append(
                {"predicted_ev_hurdle_bp": diagnostic_hurdle, **trade}
            )
        aggregate_policy_sweep.append(
            {
                "model": model_name,
                "predicted_ev_hurdle_bp": diagnostic_hurdle,
                **performance_statistics(
                    trades,
                    bootstrap_trials,
                    seed + 60_000 + index,
                ),
                "threshold_selection_counts": dict(sorted(threshold_counts.items())),
                "symbol_trade_counts": dict(sorted(symbol_counts.items())),
            }
        )

    write_csv(output_directory / "phase2_fold_metrics.csv", fold_rows)
    write_csv(output_directory / "phase2_dynamic_trades.csv.gz", dynamic_trades)
    write_csv(output_directory / "phase2_fixed_threshold_baselines.csv", baseline_rows)
    write_csv(output_directory / "phase2_logistic_coefficients.csv", coefficient_rows)
    write_csv(output_directory / "phase2_policy_hurdle_diagnostics.csv", policy_sweep_rows)
    write_csv(output_directory / "phase2_score_deciles.csv", score_bin_rows)
    write_csv(
        output_directory / "phase2_policy_hurdle_aggregate.csv",
        aggregate_policy_sweep,
    )
    write_csv(
        output_directory / "phase2_policy_sweep_trades.csv.gz",
        all_sweep_trade_rows,
    )

    result = {
        "engine_version": ENGINE_VERSION,
        "completed_utc": utc_now(),
        "samples_database": samples_database.name,
        "samples_database_sha256": sha256(samples_database),
        "config": config,
        "config_sha256": sha256(config_path),
        "feature_count": len(feature_names),
        "feature_names": feature_names,
        "walk_forward_test_period": {
            "start": config["walk_forward_folds"][0]["test_start"],
            "end_exclusive": config["walk_forward_folds"][-1]["test_end_exclusive"],
        },
        "fold_results": fold_rows,
        "aggregate_dynamic_models": aggregate_models,
        "aggregate_policy_hurdle_diagnostics": aggregate_policy_sweep,
        "fixed_threshold_baselines": baseline_rows,
        "holdout_rows_read": 0,
        "warning": (
            "All results are discovery-period walk-forward research. No model is "
            "approved for 2024 validation or live trading merely because it ranks first."
        ),
    }
    (output_directory / "phase2_walkforward_results.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


def freeze_candidate(
    samples_database: Path,
    config_path: Path,
    policy_path: Path,
    model_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    config = load_json(config_path)
    policy = load_json(policy_path)
    if policy["model"] not in config["models"]:
        raise ValueError("candidate policy names a model absent from the config")
    if policy["label"] != config["label"]:
        raise ValueError("candidate policy label differs from the modeling config")

    connection = sqlite3.connect(samples_database)
    data = pd.read_sql_query(
        "SELECT * FROM samples ORDER BY entry_timestamp_ms,candidate_id",
        connection,
    )
    metadata = {
        key: json.loads(value)
        for key, value in connection.execute("SELECT key,value_json FROM metadata")
    }
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    connection.close()
    if integrity != "ok":
        raise RuntimeError(f"samples database integrity failed: {integrity}")

    freeze_end = pd.Timestamp(policy["training_end_exclusive"])
    calibration_start = freeze_end - pd.Timedelta(
        days=int(config["calibration_lookback_days"])
    )
    freeze_end_ms = int(freeze_end.timestamp() * 1000)
    calibration_start_ms = int(calibration_start.timestamp() * 1000)
    if freeze_end > pd.Timestamp(config["discovery_end_exclusive"]):
        raise ValueError("candidate freeze crosses the discovery boundary")
    if int(data["entry_timestamp_ms"].max()) >= freeze_end_ms:
        data = data[data["entry_timestamp_ms"] < freeze_end_ms].copy()
    base_mask = data["entry_timestamp_ms"] < calibration_start_ms
    calibration_mask = (
        (data["entry_timestamp_ms"] >= calibration_start_ms)
        & (data["entry_timestamp_ms"] < freeze_end_ms)
    )
    base_data = data.loc[base_mask]
    calibration_data = data.loc[calibration_mask]
    if min(len(base_data), len(calibration_data)) == 0:
        raise RuntimeError("empty final training or calibration interval")
    features = build_feature_frame(data)
    base_weights = episode_balanced_weights(base_data)
    calibration_weights = episode_balanced_weights(calibration_data)
    estimator = make_estimator(policy["model"], config)
    fit_estimator(
        estimator,
        features.loc[base_mask],
        base_data["outcome_class"].to_numpy(dtype=int),
        base_weights,
    )
    base_calibration_probability = aligned_probabilities(
        estimator, features.loc[calibration_mask]
    )
    calibrator = fit_calibrator(
        base_calibration_probability,
        calibration_data["outcome_class"].to_numpy(dtype=int),
        calibration_weights,
        int(config["statistics"]["random_seed"]) + 90_000,
    )
    calibrated = calibrated_probabilities(
        estimator, calibrator, features.loc[calibration_mask]
    )
    payoffs, payoff_counts = payoff_lookup(
        calibration_data,
        int(config["policy"]["payoff_lookup_minimum_group_observations"]),
    )
    bundle = {
        "engine_version": ENGINE_VERSION,
        "frozen_utc": utc_now(),
        "model_name": policy["model"],
        "predicted_ev_hurdle_bp": float(policy["predicted_ev_hurdle_bp"]),
        "label": policy["label"],
        "feature_names": list(features.columns),
        "estimator": estimator,
        "calibrator": calibrator,
        "payoff_levels": payoffs,
        "payoff_counts": payoff_counts,
        "class_names": CLASS_NAMES,
        "training_start_utc": pd.to_datetime(
            int(base_data["entry_timestamp_ms"].min()), unit="ms", utc=True
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_training_end_exclusive_utc": calibration_start.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "calibration_start_utc": calibration_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "training_end_exclusive_utc": freeze_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": config,
        "policy": policy,
        "samples_database_sha256": sha256(samples_database),
        "samples_source_database_sha256": metadata["source_database_sha256"],
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, model_path, compress=3)
    restored = joblib.load(model_path)
    if restored["feature_names"] != bundle["feature_names"]:
        raise RuntimeError("frozen model reload check failed")
    manifest = {
        "engine_version": ENGINE_VERSION,
        "frozen_utc": bundle["frozen_utc"],
        "model_file": model_path.name,
        "model_file_sha256": sha256(model_path),
        "model_name": policy["model"],
        "predicted_ev_hurdle_bp": float(policy["predicted_ev_hurdle_bp"]),
        "label": policy["label"],
        "feature_count": len(bundle["feature_names"]),
        "base_training_samples": len(base_data),
        "calibration_samples": len(calibration_data),
        "base_training_end_exclusive_utc": bundle[
            "base_training_end_exclusive_utc"
        ],
        "calibration_start_utc": bundle["calibration_start_utc"],
        "training_end_exclusive_utc": bundle["training_end_exclusive_utc"],
        "calibration_log_loss": float(
            log_loss(
                calibration_data["outcome_class"].to_numpy(dtype=int),
                calibrated,
                labels=[0, 1, 2],
            )
        ),
        "samples_database": samples_database.name,
        "samples_database_sha256": bundle["samples_database_sha256"],
        "config_file": config_path.name,
        "config_file_sha256": sha256(config_path),
        "policy_file": policy_path.name,
        "policy_file_sha256": sha256(policy_path),
        "holdout_rows_read": 0,
        "status": "FROZEN_FOR_VALIDATION_NOT_APPROVED_FOR_TRADING",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def self_test() -> None:
    barriers = {25.0: 0}
    win = derive_outcome(
        25,
        1,
        barriers,
        0,
        [2],
        [4],
        [25.1],
        [25.2],
        [10.0],
        [],
        [],
        11.0,
        1.0,
        240,
    )
    assert win.outcome_class == 2 and win.exit_minutes == 2
    assert math.isclose(win.net_return_bp, 13.1)
    tie = derive_outcome(
        25,
        -1,
        barriers,
        0,
        [3],
        [3],
        [25.0],
        [25.0],
        [0.0],
        [],
        [],
        11.0,
        1.0,
        240,
    )
    assert tie.outcome_class == 0 and tie.ambiguous_same_minute == 1
    sample = pd.DataFrame(
        {
            "candidate_id": [1, 2, 3],
            "market_episode_id": ["a", "a", "b"],
        }
    )
    weights = episode_balanced_weights(sample)
    assert math.isclose(weights[0], weights[1]) and weights[2] > weights[0]
    print("self-test: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("self-test")

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source-database", type=Path, required=True)
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--output-database", type=Path, required=True)
    prepare.add_argument("--summary", type=Path, required=True)

    model = subparsers.add_parser("walk-forward")
    model.add_argument("--samples-database", type=Path, required=True)
    model.add_argument("--config", type=Path, required=True)
    model.add_argument("--output-directory", type=Path, required=True)

    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--samples-database", type=Path, required=True)
    freeze.add_argument("--config", type=Path, required=True)
    freeze.add_argument("--policy", type=Path, required=True)
    freeze.add_argument("--model", type=Path, required=True)
    freeze.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "self-test":
        self_test()
        return 0
    if args.command == "prepare":
        result = create_samples_database(
            args.source_database,
            args.config,
            args.output_database,
            args.summary,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "walk-forward":
        result = walk_forward(
            args.samples_database,
            args.config,
            args.output_directory,
        )
        print(
            json.dumps(
                {
                    "aggregate_dynamic_models": result["aggregate_dynamic_models"],
                    "holdout_rows_read": result["holdout_rows_read"],
                },
                indent=2,
            )
        )
        return 0
    if args.command == "freeze":
        result = freeze_candidate(
            args.samples_database,
            args.config,
            args.policy,
            args.model,
            args.manifest,
        )
        print(json.dumps(result, indent=2))
        return 0
    raise RuntimeError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
