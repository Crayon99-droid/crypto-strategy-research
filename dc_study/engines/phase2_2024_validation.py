#!/usr/bin/env python3
"""One-time 2024 validation of the frozen Phase 2 Candidate A policy.

This runner does not fit, calibrate, or alter a model.  It verifies the frozen
artifacts and source hashes, derives the preregistered 1:1/240-minute outcomes
from the causal event store, applies the serialized HGB/50 bp policy, and
evaluates the pass gates registered before 2024 was opened.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import accuracy_score, log_loss

import phase2_dynamic_threshold as p2


VALIDATION_START = "2024-01-01T00:00:00Z"
VALIDATION_END_EXCLUSIVE = "2025-01-01T00:00:00Z"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def verify_frozen_artifacts(
    model_path: Path,
    manifest_path: Path,
    config_path: Path,
    policy_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest = load_json(manifest_path)
    config = load_json(config_path)
    policy = load_json(policy_path)
    checks = {
        "model_sha256": sha256(model_path),
        "config_sha256": sha256(config_path),
        "policy_sha256": sha256(policy_path),
    }
    expected = {
        "model_sha256": manifest["model_file_sha256"],
        "config_sha256": manifest["config_file_sha256"],
        "policy_sha256": manifest["policy_file_sha256"],
    }
    if checks != expected:
        raise RuntimeError(
            "Frozen artifact hash mismatch: "
            + json.dumps({"actual": checks, "expected": expected}, indent=2)
        )
    if manifest["holdout_rows_read"] != 0:
        raise RuntimeError("Frozen manifest does not certify an untouched holdout")
    if manifest["training_end_exclusive_utc"] != VALIDATION_START:
        raise RuntimeError("Frozen model training boundary is not 2024-01-01")
    if policy["training_end_exclusive"] != VALIDATION_START:
        raise RuntimeError("Frozen policy training boundary is not 2024-01-01")
    if policy["predicted_ev_hurdle_bp"] != manifest["predicted_ev_hurdle_bp"]:
        raise RuntimeError("Policy and manifest EV hurdles differ")
    if policy["label"] != manifest["label"] or policy["label"] != config["label"]:
        raise RuntimeError("Frozen label definitions differ")
    return manifest, config, policy


def verify_source_files(
    data_dir: Path,
    validation_report_path: Path,
) -> dict[str, Any]:
    prior_report = load_json(validation_report_path)
    expected: dict[str, dict[str, Any]] = {}
    for group in ("candle_files", "funding_files", "instrument_files"):
        for record in prior_report[group]:
            expected[record["file"]] = record

    required = [
        "ETHUSDT_1m_2023.csv.zip",
        "ETHUSDT_1m_2024.csv.zip",
        "XRPUSDT_1m_2023.csv.zip",
        "XRPUSDT_1m_2024.csv.zip",
        "ETHUSDT_funding(1).csv",
        "XRPUSDT_funding(1).csv",
        "ETHUSDT_instrument(1).json",
        "XRPUSDT_instrument(1).json",
    ]
    rows: list[dict[str, Any]] = []
    for filename in required:
        path = data_dir / filename
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256(path)
        record = expected[filename]
        expected_hash = record["expected_sha256"]
        if actual != expected_hash:
            raise RuntimeError(f"Source hash mismatch: {filename}")
        crc_ok: bool | None = None
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                crc_ok = archive.testzip() is None
            if not crc_ok:
                raise RuntimeError(f"ZIP CRC failed: {filename}")
        rows.append(
            {
                "file": filename,
                "sha256": actual,
                "hash_matches_prior_audit": True,
                "zip_crc_pass": crc_ok,
                "prior_audit_pass": bool(record["passed"]),
                "prior_audited_rows": record.get("rows"),
                "first_timestamp_utc": record.get("first_timestamp_utc"),
                "last_timestamp_utc": record.get("last_timestamp_utc"),
            }
        )
    return {
        "prior_report_status": prior_report["summary"]["status"],
        "files": rows,
        "all_hashes_match": True,
        "all_prior_audits_passed": all(row["prior_audit_pass"] for row in rows),
    }


def load_validation_candidates(
    event_database: Path,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    connection = sqlite3.connect(event_database)
    connection.row_factory = sqlite3.Row
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"Event database integrity failed: {integrity}")
    metadata = {
        key: json.loads(value)
        for key, value in connection.execute("SELECT key,value_json FROM metadata")
    }
    if metadata["start"] != "2023-01-01T00:00:00Z":
        raise RuntimeError("Event store does not use the frozen 2023 warm-up boundary")
    if metadata["end_exclusive"] != VALIDATION_END_EXCLUSIVE:
        raise RuntimeError("Event store does not stop at the end of 2024")

    barrier_levels = [float(value) for value in metadata["barrier_levels_bp"]]
    barrier_index = {
        round(value, 8): index for index, value in enumerate(barrier_levels)
    }
    horizon_minutes = int(config["label"]["maximum_holding_minutes"])
    phase1_horizons = metadata["config"]["outcome_horizons_minutes"]
    if horizon_minutes not in phase1_horizons:
        raise RuntimeError("Frozen holding horizon is absent from the event store")
    horizon_index = phase1_horizons.index(horizon_minutes)
    start_ms = int(pd.Timestamp(VALIDATION_START).timestamp() * 1000)
    end_ms = int(pd.Timestamp(VALIDATION_END_EXCLUSIVE).timestamp() * 1000)

    query = """
        SELECT candidate_id,symbol,detector_threshold_bp,side,market_episode_id,
               signal_timestamp_ms,entry_timestamp_ms,available_minutes,
               signal_close,entry_open,entry_price,extreme_price,
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
          AND entry_timestamp_ms >= ?
          AND entry_timestamp_ms < ?
        ORDER BY entry_timestamp_ms,candidate_id
    """
    cursor = connection.execute(query, (horizon_minutes, start_ms, end_ms))
    records: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    ambiguous = 0
    for row in cursor:
        outcome = p2.derive_outcome(
            threshold_bp=int(row["detector_threshold_bp"]),
            side=int(row["side"]),
            barrier_index=barrier_index,
            horizon_index=horizon_index,
            favorable_minutes=p2.parse_json_array(
                row["favorable_first_minutes_json"]
            ),
            adverse_minutes=p2.parse_json_array(row["adverse_first_minutes_json"]),
            favorable_barriers=p2.parse_json_array(
                row["favorable_barriers_actual_bp_json"]
            ),
            adverse_barriers=p2.parse_json_array(
                row["adverse_barriers_actual_bp_json"]
            ),
            horizon_close_returns=p2.parse_json_array(
                row["close_return_horizons_bp_json"]
            ),
            funding_minutes=p2.parse_json_array(row["funding_minutes_json"]),
            funding_rates=p2.parse_json_array(row["funding_rates_json"]),
            fee_bp=float(config["execution"]["round_trip_taker_fees_bp"]),
            exit_slippage_bp=float(config["execution"]["exit_slippage_bp"]),
            horizon_minutes=horizon_minutes,
        )
        record: dict[str, Any] = {
            "candidate_id": int(row["candidate_id"]),
            "symbol": row["symbol"],
            "threshold_bp": int(row["detector_threshold_bp"]),
            "side": int(row["side"]),
            "market_episode_id": row["market_episode_id"],
            "signal_timestamp_ms": int(row["signal_timestamp_ms"]),
            "entry_timestamp_ms": int(row["entry_timestamp_ms"]),
            "signal_close": float(row["signal_close"]),
            "entry_open": float(row["entry_open"]),
            "entry_price": float(row["entry_price"]),
            "extreme_price": float(row["extreme_price"]),
            "outcome_class": outcome.outcome_class,
            "net_return_bp": outcome.net_return_bp,
            "exit_minutes": outcome.exit_minutes,
            "target_actual_bp": outcome.target_actual_bp,
            "stop_actual_bp": outcome.stop_actual_bp,
            "ambiguous_same_minute": outcome.ambiguous_same_minute,
        }
        for name in p2.RAW_FEATURE_COLUMNS:
            record[name] = row[name]
        records.append(record)
        outcome_counts[p2.CLASS_NAMES[outcome.outcome_class]] += 1
        ambiguous += outcome.ambiguous_same_minute

    total_2024_candidates = connection.execute(
        "SELECT COUNT(*) FROM candidates WHERE entry_timestamp_ms>=? AND entry_timestamp_ms<?",
        (start_ms, end_ms),
    ).fetchone()[0]
    incomplete_2024_candidates = total_2024_candidates - len(records)
    maximum_entry = connection.execute(
        "SELECT MAX(entry_timestamp_ms) FROM candidates WHERE entry_timestamp_ms<?",
        (end_ms,),
    ).fetchone()[0]
    connection.close()
    data = pd.DataFrame.from_records(records)
    if data.empty:
        raise RuntimeError("No complete 2024 validation candidates")
    if int(data["entry_timestamp_ms"].min()) < start_ms:
        raise RuntimeError("Pre-2024 candidate leaked into validation")
    if int(data["entry_timestamp_ms"].max()) >= end_ms:
        raise RuntimeError("Post-2024 candidate leaked into validation")
    return data, {
        "event_database_integrity": integrity,
        "warmup_start": metadata["start"],
        "validation_start": VALIDATION_START,
        "validation_end_exclusive": VALIDATION_END_EXCLUSIVE,
        "complete_candidates": len(data),
        "all_2024_candidates": int(total_2024_candidates),
        "incomplete_year_end_candidates_excluded": int(incomplete_2024_candidates),
        "samples_by_symbol": {
            key: int(value) for key, value in data["symbol"].value_counts().items()
        },
        "samples_by_outcome": dict(outcome_counts),
        "ambiguous_same_minute": int(ambiguous),
        "maximum_event_store_entry_before_2025_ms": int(maximum_entry),
        "post_2024_price_rows_read": 0,
    }


def enrich_trades(
    trades: list[dict[str, Any]],
    candidate_data: pd.DataFrame,
) -> list[dict[str, Any]]:
    lookup = candidate_data.set_index("candidate_id", drop=False)
    output: list[dict[str, Any]] = []
    for trade in trades:
        row = lookup.loc[int(trade["candidate_id"])]
        enriched = dict(trade)
        enriched["side_name"] = "long" if int(trade["side"]) == 1 else "short"
        enriched["signal_timestamp_utc"] = pd.to_datetime(
            int(row["signal_timestamp_ms"]), unit="ms", utc=True
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        enriched["signal_close"] = float(row["signal_close"])
        enriched["entry_open"] = float(row["entry_open"])
        enriched["entry_price"] = float(row["entry_price"])
        enriched["extreme_price"] = float(row["extreme_price"])
        enriched["target_actual_bp"] = float(row["target_actual_bp"])
        enriched["stop_actual_bp"] = float(row["stop_actual_bp"])
        enriched["ambiguous_same_minute"] = int(row["ambiguous_same_minute"])
        output.append(enriched)
    return output


def validated_performance_statistics(
    trades: list[dict[str, Any]],
    bootstrap_trials: int,
    seed: int,
) -> dict[str, Any]:
    """Use the frozen inference statistics with a nonnegative MDD correction.

    The Phase 2 helper can report a negative drawdown for an all-positive
    one-trade subgroup because its reference peak begins at zero. Drawdown is
    definitionally bounded below by zero. This descriptive correction does not
    affect any preregistered validation gate.
    """
    result = p2.performance_statistics(trades, bootstrap_trials, seed)
    value = result.get("maximum_drawdown_bp")
    if value is not None:
        result["maximum_drawdown_bp"] = max(0.0, float(value))
    return result


def grouped_statistics(
    trades: list[dict[str, Any]],
    group_name: str,
    bootstrap_trials: int,
    seed: int,
) -> list[dict[str, Any]]:
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    if group_name == "month":
        for trade in trades:
            key = pd.to_datetime(
                trade["entry_timestamp_ms"], unit="ms", utc=True
            ).strftime("%Y-%m")
            groups[key].append(trade)
    else:
        for trade in trades:
            groups[str(trade[group_name])].append(trade)
    rows: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(groups)):
        rows.append(
            {
                group_name: key,
                **validated_performance_statistics(
                    groups[key], bootstrap_trials, seed + 100 + index
                ),
            }
        )
    return rows


def score_deciles(data: pd.DataFrame, predicted_ev: np.ndarray) -> list[dict[str, Any]]:
    frame = pd.DataFrame(
        {
            "predicted_ev_bp": predicted_ev,
            "actual_net_return_bp": data["net_return_bp"].to_numpy(dtype=float),
            "outcome_class": data["outcome_class"].to_numpy(dtype=int),
        }
    )
    frame["score_decile"] = pd.qcut(
        frame["predicted_ev_bp"], 10, labels=False, duplicates="drop"
    )
    rows: list[dict[str, Any]] = []
    for decile, group in frame.groupby("score_decile", observed=True):
        rows.append(
            {
                "score_decile": int(decile),
                "candidates": int(len(group)),
                "mean_predicted_ev_bp": float(group["predicted_ev_bp"].mean()),
                "mean_actual_net_return_bp": float(
                    group["actual_net_return_bp"].mean()
                ),
                "tp_first_rate": float((group["outcome_class"] == 2).mean()),
                "sl_first_rate": float((group["outcome_class"] == 0).mean()),
                "timeout_rate": float((group["outcome_class"] == 1).mean()),
            }
        )
    return rows


def evaluate_gates(
    aggregate: dict[str, Any],
    by_symbol: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    rule = policy["validation_rule"]
    minimum_trades_pass = aggregate["trades"] >= int(rule["minimum_completed_trades"])
    mean_pass = (
        aggregate["mean_net_return_bp"]
        > float(rule["continuation_gate"]["mean_net_return_bp_must_exceed"])
    )
    profit_factor = aggregate["profit_factor"]
    profit_factor_pass = (
        profit_factor is not None
        and profit_factor
        > float(rule["continuation_gate"]["profit_factor_must_exceed"])
    )
    continuation_pass = minimum_trades_pass and mean_pass and profit_factor_pass

    cluster_lower = aggregate["weekly_cluster_lower_95_bp"]
    bootstrap_lower = aggregate["bootstrap_mean_lower_95_bp"]
    cluster_pass = (
        cluster_lower is not None
        and cluster_lower
        > float(
            rule["statistical_edge_gate"][
                "weekly_cluster_lower_95_bp_must_exceed"
            ]
        )
    )
    bootstrap_pass = (
        bootstrap_lower is not None
        and bootstrap_lower
        > float(
            rule["statistical_edge_gate"][
                "weekly_block_bootstrap_lower_95_bp_must_exceed"
            ]
        )
    )
    symbol_map = {row["symbol"]: row for row in by_symbol}
    cross_asset_applicable = all(
        symbol_map.get(symbol, {}).get("trades", 0) >= 20
        for symbol in ("ETHUSDT", "XRPUSDT")
    )
    cross_asset_pass = (
        all(
            symbol_map[symbol]["mean_net_return_bp"] > 0.0
            for symbol in ("ETHUSDT", "XRPUSDT")
        )
        if cross_asset_applicable
        else True
    )
    statistical_edge_pass = (
        continuation_pass and cluster_pass and bootstrap_pass and cross_asset_pass
    )
    if not continuation_pass:
        decision = "REJECT_CANDIDATE_A"
    elif not statistical_edge_pass:
        decision = "RETAIN_AS_UNCONFIRMED_RESEARCH_CANDIDATE"
    else:
        decision = "PASSES_2024_STATISTICAL_EDGE_GATE"
    return {
        "minimum_completed_trades": {
            "required": int(rule["minimum_completed_trades"]),
            "actual": int(aggregate["trades"]),
            "pass": minimum_trades_pass,
        },
        "continuation_gate": {
            "mean_net_return_positive": mean_pass,
            "profit_factor_above_one": profit_factor_pass,
            "pass": continuation_pass,
        },
        "statistical_edge_gate": {
            "weekly_cluster_lower_95_positive": cluster_pass,
            "weekly_block_bootstrap_lower_95_positive": bootstrap_pass,
            "cross_asset_rule_applicable": cross_asset_applicable,
            "cross_asset_rule_pass": cross_asset_pass,
            "pass": statistical_edge_pass,
        },
        "decision": decision,
        "live_trading_allowed": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_directory
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest, config, policy = verify_frozen_artifacts(
        args.model, args.manifest, args.config, args.policy
    )
    source_audit = verify_source_files(args.data_dir, args.source_audit)
    model_hash_before = sha256(args.model)
    bundle = joblib.load(args.model)
    if bundle["training_end_exclusive_utc"] != VALIDATION_START:
        raise RuntimeError("Serialized bundle training boundary differs")
    if float(bundle["predicted_ev_hurdle_bp"]) != float(
        policy["predicted_ev_hurdle_bp"]
    ):
        raise RuntimeError("Serialized bundle hurdle differs")

    data, event_audit = load_validation_candidates(args.event_database, config)
    features = p2.build_feature_frame(data)
    if list(features.columns) != list(bundle["feature_names"]):
        raise RuntimeError("Validation feature schema differs from frozen schema")
    probabilities = p2.calibrated_probabilities(
        bundle["estimator"], bundle["calibrator"], features
    )
    probabilities_repeat = p2.calibrated_probabilities(
        bundle["estimator"], bundle["calibrator"], features
    )
    if not np.array_equal(probabilities, probabilities_repeat):
        raise RuntimeError("Frozen predictions are not deterministic")
    payoff_values = p2.payoff_matrix(
        data, bundle["payoff_levels"], bundle["payoff_counts"]
    )
    predicted_ev = np.sum(probabilities * payoff_values, axis=1)
    hurdle = float(policy["predicted_ev_hurdle_bp"])
    trades = p2.simulate_policy(
        data,
        probabilities,
        predicted_ev,
        bundle["model_name"],
        "2024_VALIDATION",
        hurdle,
    )
    trades = enrich_trades(trades, data)

    bootstrap_trials = int(config["statistics"]["weekly_block_bootstrap_trials"])
    seed = int(config["statistics"]["random_seed"])
    aggregate = validated_performance_statistics(trades, bootstrap_trials, seed)
    by_symbol = grouped_statistics(
        trades, "symbol", bootstrap_trials, seed + 1_000
    )
    by_threshold = grouped_statistics(
        trades, "threshold_bp", bootstrap_trials, seed + 2_000
    )
    by_month = grouped_statistics(trades, "month", bootstrap_trials, seed + 3_000)
    gates = evaluate_gates(aggregate, by_symbol, policy)

    threshold_counts = Counter(int(row["threshold_bp"]) for row in trades)
    side_counts = Counter(row["side_name"] for row in trades)
    model_hash_after = sha256(args.model)
    if model_hash_after != model_hash_before:
        raise RuntimeError("Frozen model bytes changed during validation")

    write_csv(output_dir / "phase2_2024_validation_trades.csv", trades)
    write_csv(output_dir / "phase2_2024_by_symbol.csv", by_symbol)
    write_csv(output_dir / "phase2_2024_by_threshold.csv", by_threshold)
    write_csv(output_dir / "phase2_2024_by_month.csv", by_month)
    deciles = score_deciles(data, predicted_ev)
    write_csv(output_dir / "phase2_2024_score_deciles.csv", deciles)
    write_csv(output_dir / "phase2_2024_source_audit.csv", source_audit["files"])

    classification = {
        "candidate_level_log_loss": float(
            log_loss(
                data["outcome_class"].to_numpy(dtype=int),
                probabilities,
                labels=[0, 1, 2],
            )
        ),
        "candidate_level_accuracy": float(
            accuracy_score(
                data["outcome_class"].to_numpy(dtype=int),
                np.argmax(probabilities, axis=1),
            )
        ),
        "eligible_candidate_rows": int((predicted_ev >= hurdle).sum()),
        "candidate_rows": int(len(data)),
    }
    result = {
        "validation_name": "Frozen Candidate A one-time 2024 validation",
        "completed_utc": p2.utc_now(),
        "decision": gates["decision"],
        "validation_period": {
            "start": VALIDATION_START,
            "end_exclusive": VALIDATION_END_EXCLUSIVE,
            "start_flat": True,
            "complete_240_minute_outcomes_only": True,
        },
        "frozen_policy": {
            "model": policy["model"],
            "predicted_ev_hurdle_bp": hurdle,
            "reward_risk": policy["label"]["reward_risk"],
            "maximum_holding_minutes": policy["label"][
                "maximum_holding_minutes"
            ],
            "same_minute_tp_sl_policy": policy["label"][
                "same_minute_tp_sl_policy"
            ],
            "maximum_open_positions_per_symbol": policy["execution_policy"][
                "maximum_open_positions_per_symbol"
            ],
            "parameters_changed": False,
            "retrained": False,
        },
        "aggregate_performance": aggregate,
        "performance_by_symbol": by_symbol,
        "performance_by_threshold": by_threshold,
        "threshold_selection_counts": dict(sorted(threshold_counts.items())),
        "side_counts": dict(sorted(side_counts.items())),
        "classification_diagnostics": classification,
        "gates": gates,
        "event_store_audit": event_audit,
        "source_audit": source_audit,
        "frozen_artifact_audit": {
            "model_sha256_before": model_hash_before,
            "model_sha256_after": model_hash_after,
            "manifest_sha256": sha256(args.manifest),
            "config_sha256": sha256(args.config),
            "policy_sha256": sha256(args.policy),
            "feature_schema_exact_match": True,
            "prediction_repeat_exact_match": True,
            "holdout_rows_read_before_freeze": manifest["holdout_rows_read"],
        },
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "joblib": joblib.__version__,
            "platform": platform.platform(),
        },
        "locked_test": {
            "period": "2025-01-01 through 2026-08-22",
            "price_rows_read": 0,
            "status": "UNTOUCHED",
        },
        "warning": "A 2024 pass does not authorize live trading; 2025-2026 remains locked for the final test.",
    }
    output_json = output_dir / "PHASE2_2024_VALIDATION_RESULTS.json"
    output_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-database", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--source-audit", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    result = run(parse_args())
    print(
        json.dumps(
            {
                "decision": result["decision"],
                "aggregate_performance": result["aggregate_performance"],
                "gates": result["gates"],
                "threshold_selection_counts": result[
                    "threshold_selection_counts"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
