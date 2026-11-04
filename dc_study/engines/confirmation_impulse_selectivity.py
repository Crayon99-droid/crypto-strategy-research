#!/usr/bin/env python3
"""Preregistered causal confirmation-impulse selectivity test."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "CONFIRMATION_IMPULSE_PROTOCOL.json"
RESULTS_PATH = HERE / "CONFIRMATION_IMPULSE_RESULTS.json"
EVENT_AUDIT_PATH = HERE / "confirmation_impulse_event_audit.jsonl.gz"
TRADE_AUDIT_PATH = HERE / "confirmation_impulse_selected_trades.jsonl.gz"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iso_ms(value: str) -> int:
    return int(pd.Timestamp(value).timestamp() * 1000)


def json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def json_safe(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def deterministic_jsonl_gzip(frame: pd.DataFrame, path: Path) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as zipped:
            for record in frame.to_dict("records"):
                safe_record = {key: json_safe(value) for key, value in record.items()}
                line = json.dumps(safe_record, separators=(",", ":"), allow_nan=False, default=json_default)
                zipped.write(line.encode("utf-8") + b"\n")


def profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    return gains / losses if losses > 0 else None


def select_nonoverlap(frame: pd.DataFrame) -> pd.DataFrame:
    selected: list[int] = []
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
            selected.append(index)
            busy = int(until)
    return frame.loc[selected].sort_values(["symbol", "entry_timestamp_ms", "candidate_id"]).copy()


def load_candidates(path: Path, multiplier: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    connection = sqlite3.connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity failed: {integrity}")
        frame = pd.read_sql_query(
            "SELECT * FROM candidates WHERE ABS(detector_multiplier - ?) < 1e-12 "
            "ORDER BY symbol, signal_timestamp_ms, candidate_id",
            connection,
            params=(multiplier,),
        )
        metadata = {
            key: json.loads(value)
            for key, value in connection.execute("SELECT key,value_json FROM metadata")
        }
    finally:
        connection.close()
    return frame, metadata


def verify_database_archive(database: Path, archive: Path, expected_archive_hash: str) -> dict[str, Any]:
    archive_hash = sha256(archive)
    if archive_hash != expected_archive_hash:
        raise RuntimeError("Candidate archive hash mismatch")
    digest = hashlib.sha256()
    with gzip.open(archive, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    current_hash = sha256(database)
    if digest.hexdigest() != current_hash:
        raise RuntimeError("Candidate database does not match its frozen archive")
    return {
        "archive_sha256": archive_hash,
        "database_sha256": current_hash,
        "database_matches_frozen_archive": True,
    }


def load_close_series(data_dir: Path, symbol: str, years: list[int]) -> pd.DataFrame:
    parts = []
    for year in years:
        path = data_dir / f"{symbol}_1m_{year}.csv.zip"
        parts.append(pd.read_csv(path, compression="zip", usecols=["timestamp_ms", "close"]))
    return pd.concat(parts, ignore_index=True)


def add_feature(candidates: pd.DataFrame, data_dir: Path, protocol: dict[str, Any]) -> pd.DataFrame:
    pieces = []
    feature = protocol["feature"]
    filter_cfg = protocol["filter"]
    years = [int(year) for year in protocol["inputs"]["candle_years"]]
    for symbol, group in candidates.groupby("symbol", sort=True):
        bars = load_close_series(data_dir, symbol, years)
        timestamps = bars["timestamp_ms"].to_numpy(np.int64)
        close = bars["close"].astype(float)
        returns = np.log(close).diff()
        sigma = (
            returns.ewm(
                halflife=float(feature["ewma_sigma"]["half_life_minutes"]),
                adjust=bool(feature["ewma_sigma"]["adjust"]),
                min_periods=int(feature["ewma_sigma"]["minimum_prior_observations"]),
            )
            .std(bias=False)
            .shift(1)
        )
        output = group.copy()
        index = output["signal_index"].to_numpy(np.int64)
        if not np.array_equal(timestamps[index], output["signal_timestamp_ms"].to_numpy(np.int64)):
            raise RuntimeError(f"{symbol} candidate-to-candle timestamp mismatch")
        if not np.allclose(close.to_numpy(float)[index], output["signal_close"].to_numpy(float), rtol=0, atol=1e-12):
            raise RuntimeError(f"{symbol} candidate-to-candle close mismatch")
        impulse = output["side"].to_numpy(float) * returns.to_numpy(float)[index] / sigma.to_numpy(float)[index]
        output["confirmation_impulse"] = impulse
        event_series = pd.Series(
            impulse,
            index=pd.to_datetime(output["signal_timestamp_ms"].to_numpy(np.int64), unit="ms", utc=True),
        )
        cutoff = event_series.rolling(
            f"{int(filter_cfg['lookback_calendar_days'])}D",
            closed="left",
            min_periods=int(filter_cfg["minimum_prior_events"]),
        ).quantile(0.75)
        output["prior_event_impulse_p75"] = cutoff.to_numpy(float)
        output["selectivity_ready"] = np.isfinite(output["confirmation_impulse"]) & np.isfinite(
            output["prior_event_impulse_p75"]
        )
        output["selectivity_pass"] = output["selectivity_ready"] & (
            output["confirmation_impulse"] >= output["prior_event_impulse_p75"]
        )
        pieces.append(output)
    return pd.concat(pieces, ignore_index=True).sort_values(
        ["symbol", "signal_timestamp_ms", "candidate_id"]
    ).reset_index(drop=True)


def moving_block_indices(n_weeks: int, block_weeks: int, replicates: int, seed: int) -> np.ndarray:
    starts = np.arange(n_weeks - block_weeks + 1, dtype=np.int32)
    needed = math.ceil(n_weeks / block_weeks)
    rng = np.random.default_rng(seed)
    sampled = rng.choice(starts, size=(replicates, needed), replace=True)
    offsets = np.arange(block_weeks, dtype=np.int32)
    return (sampled[:, :, None] + offsets).reshape(replicates, -1)[:, :n_weeks]


def weekly_arrays(frame: pd.DataFrame, weeks: np.ndarray, column: str) -> tuple[np.ndarray, np.ndarray]:
    table = frame.groupby("week_cluster", sort=True)[column].agg(["sum", "count"])
    table = table.reindex(weeks, fill_value=0)
    return table["sum"].to_numpy(float), table["count"].to_numpy(float)


def bootstrap_ratio(sums: np.ndarray, counts: np.ndarray, sampled: np.ndarray) -> np.ndarray:
    total = sums[sampled].sum(axis=1)
    n = counts[sampled].sum(axis=1)
    if bool((n == 0).any()):
        raise RuntimeError("A bootstrap replicate has no selected trades")
    return total / n


def metric_summary(frame: pd.DataFrame, value_column: str) -> dict[str, Any]:
    values = frame[value_column].to_numpy(float)
    positives = np.sort(values[values > 0])[::-1]
    top_n = max(1, math.ceil(len(values) * 0.05))
    concentration = float(positives[:top_n].sum() / positives.sum()) if positives.size else None
    return {
        "trades": int(len(frame)),
        "unique_trade_weeks": int(frame["week_cluster"].nunique()),
        "mean_net_bp": float(values.mean()),
        "total_net_bp": float(values.sum()),
        "profit_factor": profit_factor(values),
        "positive_trade_rate": float((values > 0).mean()),
        "mean_signal_path_gross_bp": float(frame["signal_path_gross_bp"].mean()),
        "mean_execution_cost_bp": float(frame["execution_cost_bp"].mean()),
        "mean_funding_bp": float(frame["funding_bp"].mean()),
        "tp_first_rate": float((frame["outcome"] == "tp_first").mean()),
        "sl_first_rate": float((frame["outcome"] == "sl_first").mean()),
        "timeout_rate": float((frame["outcome"] == "timeout").mean()),
        "best_5pct_share_of_total_positive_pnl": concentration,
    }


def grouped_metrics(frame: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    rows = []
    for keys, group in frame.groupby(columns, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        values = group["net_return_bp"].to_numpy(float)
        item = {
            column: key.item() if isinstance(key, np.generic) else key
            for column, key in zip(columns, keys)
        }
        item.update({
            "trades": int(len(group)),
            "mean_net_bp": float(values.mean()),
            "profit_factor": profit_factor(values),
            "positive_trade_rate": float((values > 0).mean()),
        })
        rows.append(item)
    return rows


def main() -> None:
    protocol = json.loads(PROTOCOL_PATH.read_text())
    if not protocol.get("frozen_before_selectivity_outcomes"):
        raise RuntimeError("Protocol is not frozen")
    inputs = protocol["inputs"]
    database = ROOT / inputs["candidate_database"]
    archive = ROOT / inputs["candidate_archive"]
    baseline_file = ROOT / inputs["baseline_trade_file"]
    data_dir = ROOT / inputs["data_directory"]

    database_check = verify_database_archive(
        database, archive, inputs["candidate_archive_expected_sha256"]
    )
    baseline_hash = sha256(baseline_file)
    if baseline_hash != inputs["baseline_trade_expected_sha256"]:
        raise RuntimeError("Frozen baseline trade hash mismatch")
    candidates, metadata = load_candidates(
        database, float(protocol["frozen_strategy"]["detector_multiplier"])
    )
    if len(candidates) != 14_738:
        raise RuntimeError("Unexpected fixed-k candidate count")

    source_checks = []
    for filename, expected in metadata["source_hashes"].items():
        path = data_dir / filename
        actual = sha256(path)
        source_checks.append({"file": filename, "sha256": actual, "match": actual == expected})
    if not all(item["match"] for item in source_checks):
        raise RuntimeError("Source hash validation failed")
    if int(candidates["signal_timestamp_ms"].max()) >= iso_ms("2025-01-01T00:00:00Z"):
        raise RuntimeError("Locked 2025+ candidate encountered")

    featured = add_feature(candidates, data_dir, protocol)
    folds = protocol["development_folds"]
    filtered_parts = []
    baseline_parts = []
    for fold in folds:
        start, end = iso_ms(fold["start"]), iso_ms(fold["end"])
        test = featured.loc[
            (featured["entry_timestamp_ms"] >= start) & (featured["exit_timestamp_ms"] < end)
        ].copy()
        baseline = select_nonoverlap(test)
        baseline["fold"] = fold["name"]
        selected = select_nonoverlap(test.loc[test["selectivity_pass"]].copy())
        selected["fold"] = fold["name"]
        baseline_parts.append(baseline)
        filtered_parts.append(selected)
    baseline = pd.concat(baseline_parts, ignore_index=True)
    selected = pd.concat(filtered_parts, ignore_index=True)

    frozen_baseline = pd.read_csv(baseline_file)
    frozen_baseline = frozen_baseline[frozen_baseline["policy"].eq("fixed_k_1.25")].copy()
    if baseline["candidate_id"].tolist() != frozen_baseline["candidate_id"].tolist():
        raise RuntimeError("Causal baseline candidate IDs do not reproduce Phase 5")
    if not np.allclose(
        baseline["net_return_bp"].to_numpy(float),
        frozen_baseline["net_return_bp"].to_numpy(float),
        rtol=0,
        atol=1e-12,
    ):
        raise RuntimeError("Causal baseline returns do not reproduce Phase 5")

    weeks = np.arange(
        int(frozen_baseline["week_cluster"].min()),
        int(frozen_baseline["week_cluster"].max()) + 1,
        dtype=np.int64,
    )
    inference = protocol["inference"]
    sampled = moving_block_indices(
        len(weeks),
        int(inference["block_weeks"]),
        int(inference["replicates"]),
        int(inference["random_seed"]),
    )
    selected_sums, selected_counts = weekly_arrays(selected, weeks, "net_return_bp")
    baseline_sums, baseline_counts = weekly_arrays(baseline, weeks, "net_return_bp")
    selected_boot = bootstrap_ratio(selected_sums, selected_counts, sampled)
    baseline_boot = bootstrap_ratio(baseline_sums, baseline_counts, sampled)
    paired_boot = selected_boot - baseline_boot
    lower_q = float(1.0 - inference["one_sided_lower_confidence"])

    selected_metrics = metric_summary(selected, "net_return_bp")
    baseline_metrics = metric_summary(baseline, "net_return_bp")
    selected_metrics.update({
        "bootstrap_lower_98_one_sided_bp": float(np.quantile(selected_boot, lower_q)),
        "bootstrap_lower_95_bp": float(np.quantile(selected_boot, 0.025)),
        "bootstrap_upper_95_bp": float(np.quantile(selected_boot, 0.975)),
        "paired_mean_improvement_bp": float(selected["net_return_bp"].mean() - baseline["net_return_bp"].mean()),
        "paired_bootstrap_lower_98_one_sided_bp": float(np.quantile(paired_boot, lower_q)),
        "additional_2bp_stress_mean_bp": float((selected["net_return_bp"] - 2.0).mean()),
    })

    selected = selected.copy()
    selected["year"] = pd.to_datetime(selected["entry_timestamp_ms"], unit="ms", utc=True).dt.year
    halfyear = grouped_metrics(selected, ["halfyear"])
    year = grouped_metrics(selected, ["year"])
    symbol_side = grouped_metrics(selected, ["symbol", "side"])
    counts_symbol = selected["symbol"].value_counts()
    counts_side = selected["side"].value_counts()
    gate_cfg = protocol["evidence_gates"]
    gate_rows = [
        {"gate": "minimum_nonoverlap_trades", "passed": len(selected) >= gate_cfg["minimum_nonoverlap_trades"], "value": len(selected)},
        {"gate": "minimum_unique_trade_weeks", "passed": selected["week_cluster"].nunique() >= gate_cfg["minimum_unique_trade_weeks"], "value": int(selected["week_cluster"].nunique())},
        {"gate": "minimum_trades_each_asset", "passed": min(int(counts_symbol.get(s, 0)) for s in inputs["symbols"]) >= gate_cfg["minimum_trades_each_asset"], "value": {s: int(counts_symbol.get(s, 0)) for s in inputs["symbols"]}},
        {"gate": "minimum_trades_each_side", "passed": min(int(counts_side.get(s, 0)) for s in (-1, 1)) >= gate_cfg["minimum_trades_each_side"], "value": {"short": int(counts_side.get(-1, 0)), "long": int(counts_side.get(1, 0))}},
        {"gate": "minimum_profit_factor", "passed": selected_metrics["profit_factor"] >= gate_cfg["minimum_profit_factor"], "value": selected_metrics["profit_factor"]},
        {"gate": "positive_2023_and_2024", "passed": all(item["mean_net_bp"] > 0 for item in year) and len(year) == 2, "value": year},
        {"gate": "minimum_positive_halfyears", "passed": sum(item["mean_net_bp"] > 0 for item in halfyear) >= gate_cfg["minimum_positive_halfyears"], "value": sum(item["mean_net_bp"] > 0 for item in halfyear)},
        {"gate": "absolute_98_lower_bound", "passed": selected_metrics["bootstrap_lower_98_one_sided_bp"] > gate_cfg["absolute_98_lower_bound_above_bp"], "value": selected_metrics["bootstrap_lower_98_one_sided_bp"]},
        {"gate": "paired_98_lower_bound", "passed": selected_metrics["paired_bootstrap_lower_98_one_sided_bp"] > gate_cfg["paired_98_lower_bound_above_bp"], "value": selected_metrics["paired_bootstrap_lower_98_one_sided_bp"]},
        {"gate": "additional_2bp_cost_stress", "passed": selected_metrics["additional_2bp_stress_mean_bp"] > gate_cfg["stressed_mean_above_bp"], "value": selected_metrics["additional_2bp_stress_mean_bp"]},
        {"gate": "pnl_concentration", "passed": selected_metrics["best_5pct_share_of_total_positive_pnl"] <= gate_cfg["maximum_best_5pct_share_of_total_positive_pnl"], "value": selected_metrics["best_5pct_share_of_total_positive_pnl"]},
    ]
    passed = all(bool(row["passed"]) for row in gate_rows)

    event_audit = featured[[
        "candidate_id", "symbol", "side", "signal_timestamp_ms", "signal_index",
        "confirmation_impulse", "prior_event_impulse_p75", "selectivity_ready", "selectivity_pass",
    ]].copy()
    deterministic_jsonl_gzip(event_audit, EVENT_AUDIT_PATH)
    selected_audit = selected.copy()
    deterministic_jsonl_gzip(selected_audit, TRADE_AUDIT_PATH)

    payload = {
        "protocol": protocol,
        "integrity": {
            **database_check,
            "baseline_trade_sha256": baseline_hash,
            "baseline_hash_match": True,
            "source_files_checked": len(source_checks),
            "all_source_hashes_match": True,
            "candidate_database_integrity": "ok",
            "fixed_k_candidate_events": len(featured),
            "baseline_trade_rows_reproduced": len(baseline),
            "baseline_candidate_ids_exact_match": True,
            "baseline_returns_exact_match": True,
            "locked_2025_2026_rows_read": 0,
        },
        "feature_diagnostics": {
            "ready_events": int(featured["selectivity_ready"].sum()),
            "passing_events": int(featured["selectivity_pass"].sum()),
            "passing_share_of_ready_events": float(featured.loc[featured["selectivity_ready"], "selectivity_pass"].mean()),
            "selected_nonoverlap_trades": len(selected),
            "median_confirmation_impulse_selected": float(selected["confirmation_impulse"].median()),
            "median_cutoff_selected": float(selected["prior_event_impulse_p75"].median()),
        },
        "baseline_metrics": baseline_metrics,
        "selected_metrics": selected_metrics,
        "halfyear_metrics": halfyear,
        "year_metrics": year,
        "symbol_side_metrics": symbol_side,
        "evidence_gates": gate_rows,
        "all_gates_passed": passed,
        "decision": "FREEZE_FOR_LOCKED_TEST" if passed else "FAIL_OHLC_SELECTIVITY__MOVE_TO_BTC_LEAD_LAG",
        "audit_artifacts": {
            "event_audit": EVENT_AUDIT_PATH.name,
            "selected_trade_audit": TRADE_AUDIT_PATH.name,
        },
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, allow_nan=False, default=json_default) + "\n")
    print(json.dumps({
        "feature_diagnostics": payload["feature_diagnostics"],
        "baseline_metrics": baseline_metrics,
        "selected_metrics": selected_metrics,
        "evidence_gates": gate_rows,
        "decision": payload["decision"],
    }, indent=2, allow_nan=False, default=json_default))


if __name__ == "__main__":
    main()
