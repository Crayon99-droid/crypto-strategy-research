#!/usr/bin/env python3
"""Frozen H2 BTC-to-ETH/XRP lead-lag evaluation.

Implements exactly one causal BTC feature/filter across the nine independent
Phase-5 detector streams. The script refuses mismatched frozen inputs, verifies
all fixed-k baselines, applies filtering before non-overlap selection, and uses
simultaneous one-sided 98% four-week circular moving-block max-t inference for
both absolute performance and paired improvement.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROTOCOL_SHA256 = "a97797d07714679e88d4f9aed7a0ea7062d1eec54e5e45a6ffdc3eec5a9202ff"
CANDIDATE_ARCHIVE_SHA256 = "291f6f90d7f5af8306c8aa9a52954b0f36cf2db6796e59221a94498018909f89"
PHASE5_CONFIG_SHA256 = "a5757b172163ac38a49f722e924795d04e69dd19ff62241af4910fad5dd34a53"
BASELINE_TRADES_SHA256 = "853f554466e6d6892304774aba28562b9f2d78b02862039da4eb8700172f9a8f"
MINUTE_MS = 60_000
WEEK_MS = 7 * 24 * 60 * MINUTE_MS
EVAL_START_MS = int(pd.Timestamp("2023-01-01T00:00:00Z").timestamp() * 1000)
EVAL_END_MS = int(pd.Timestamp("2025-01-01T00:00:00Z").timestamp() * 1000)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def json_default(value: Any) -> Any:
    converted = safe(value)
    if converted is value:
        raise TypeError(type(value).__name__)
    return converted


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=json_default)
        + "\n",
        encoding="utf-8",
    )


def deterministic_jsonl_gzip(frame: pd.DataFrame, path: Path) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0
        ) as zipped:
            for record in frame.to_dict("records"):
                converted = {key: safe(value) for key, value in record.items()}
                zipped.write(
                    json.dumps(
                        converted,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                        default=json_default,
                    ).encode("utf-8")
                    + b"\n"
                )


def deterministic_json_gzip(value: Any, path: Path) -> None:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=json_default
    ).encode("utf-8")
    with path.open("wb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0
        ) as zipped:
            zipped.write(payload)


def verify_inputs(args: argparse.Namespace) -> dict[str, Any]:
    hashes = {
        "protocol": sha256(args.protocol),
        "phase5_config": sha256(args.phase5_config),
        "candidate_archive": sha256(args.candidate_archive),
        "baseline_trades": sha256(args.baseline_trades),
        "btc_source_manifest": sha256(args.btc_source_manifest),
        "btc_source_audit": sha256(args.btc_source_audit),
        "btc_ledger_audit": sha256(args.btc_ledger_audit),
    }
    if hashes["protocol"] != PROTOCOL_SHA256:
        raise RuntimeError("Frozen H2 protocol SHA-256 mismatch")
    if hashes["phase5_config"] != PHASE5_CONFIG_SHA256:
        raise RuntimeError("Frozen Phase 5 config SHA-256 mismatch")
    if hashes["candidate_archive"] != CANDIDATE_ARCHIVE_SHA256:
        raise RuntimeError("Frozen Phase 5 candidate archive SHA-256 mismatch")
    if hashes["baseline_trades"] != BASELINE_TRADES_SHA256:
        raise RuntimeError("Frozen Phase 5 baseline-trade SHA-256 mismatch")
    source_audit = json.loads(args.btc_source_audit.read_text(encoding="utf-8"))
    ledger_audit = json.loads(args.btc_ledger_audit.read_text(encoding="utf-8"))
    if not source_audit.get("passed") or not ledger_audit.get("passed"):
        raise RuntimeError("BTC source or request-ledger audit did not pass")
    if int(source_audit.get("total_rows", -1)) != 2_103_840:
        raise RuntimeError("Unexpected BTC candle count")
    if int(ledger_audit.get("rows", -1)) != 2_103_840:
        raise RuntimeError("Unexpected BTC ledger coverage")
    manifest_hash = hashes["btc_source_manifest"]
    if source_audit.get("provenance_manifest", {}).get("sha256") != manifest_hash:
        raise RuntimeError("BTC source audit does not refer to the supplied manifest")
    if ledger_audit.get("manifest_sha256") != manifest_hash:
        raise RuntimeError("BTC ledger audit does not refer to the supplied manifest")
    return hashes


def verify_database(database: Path, archive: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with gzip.open(archive, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    database_hash = sha256(database)
    if digest.hexdigest() != database_hash:
        raise RuntimeError("Uncompressed candidate database differs from frozen archive")
    connection = sqlite3.connect(database)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        count = int(connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0])
        locked = int(
            connection.execute(
                "SELECT COUNT(*) FROM candidates WHERE entry_timestamp_ms >= ?",
                (EVAL_END_MS,),
            ).fetchone()[0]
        )
        metadata = {
            key: json.loads(value)
            for key, value in connection.execute("SELECT key,value_json FROM metadata")
        }
    finally:
        connection.close()
    if integrity != "ok" or count != 388_338 or locked != 0:
        raise RuntimeError("Candidate database integrity, count, or locked-period check failed")
    return {
        "database_sha256": database_hash,
        "sqlite_integrity": integrity,
        "candidate_rows": count,
        "candidate_rows_2025_or_later": locked,
        "metadata_engine_version": metadata.get("engine_version"),
        "metadata_config_sha256": metadata.get("config_sha256"),
    }


def load_candidates(database: Path) -> pd.DataFrame:
    connection = sqlite3.connect(database)
    try:
        frame = pd.read_sql_query(
            "SELECT * FROM candidates ORDER BY symbol,detector_multiplier,signal_timestamp_ms,candidate_id",
            connection,
        )
    finally:
        connection.close()
    duplicate = frame.duplicated(
        ["symbol", "detector_multiplier", "signal_timestamp_ms"], keep=False
    )
    if bool(duplicate.any()):
        raise RuntimeError("Duplicate event timestamp within a symbol/multiplier stream")
    return frame


def load_btc(data_dir: Path, manifest_path: Path) -> pd.DataFrame:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {item["file"]: item["sha256"] for item in manifest["archives"]}
    parts = []
    for year in (2021, 2022, 2023, 2024):
        path = data_dir / f"BTCUSDT_1m_{year}.csv.zip"
        if sha256(path) != expected.get(path.name):
            raise RuntimeError(f"BTC archive hash mismatch during feature load: {path.name}")
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise RuntimeError(f"BTC archive CRC failed during feature load: {path.name}")
        parts.append(
            pd.read_csv(
                path,
                compression="zip",
                usecols=["timestamp_ms", "close"],
                dtype={"timestamp_ms": "int64", "close": "float64"},
            )
        )
    bars = pd.concat(parts, ignore_index=True)
    if len(bars) != 2_103_840:
        raise RuntimeError("BTC feature frame has the wrong number of rows")
    timestamps = bars["timestamp_ms"].to_numpy(np.int64)
    if not bool(np.all(np.diff(timestamps) == MINUTE_MS)):
        raise RuntimeError("BTC feature timestamps are not one-minute contiguous")
    log_close = np.log(bars["close"].to_numpy(float))
    returns = np.empty(len(log_close), dtype=float)
    returns[:] = np.nan
    returns[1:] = np.diff(log_close)
    r5 = np.empty(len(log_close), dtype=float)
    r5[:] = np.nan
    r5[5:] = log_close[5:] - log_close[:-5]
    rv240 = (
        pd.Series(np.square(returns))
        .rolling(window=240, min_periods=240)
        .sum()
        .pow(0.5)
        .to_numpy(float)
    )
    bars["btc_log_return_5m"] = r5
    bars["btc_realized_volatility_240m"] = rv240
    return bars


def add_btc_feature(
    candidates: pd.DataFrame, btc: pd.DataFrame, protocol: dict[str, Any]
) -> pd.DataFrame:
    output = candidates.copy()
    btc_time = btc["timestamp_ms"].to_numpy(np.int64)
    first = int(btc_time[0])
    event_time = output["signal_timestamp_ms"].to_numpy(np.int64)
    offsets = (event_time - first) // MINUTE_MS
    if bool(((event_time - first) % MINUTE_MS != 0).any()):
        raise RuntimeError("Candidate signal timestamp is off the BTC minute grid")
    if bool((offsets < 0).any()) or bool((offsets >= len(btc)).any()):
        raise RuntimeError("Candidate signal timestamp falls outside BTC coverage")
    if not np.array_equal(btc_time[offsets], event_time):
        raise RuntimeError("Exact BTC/target timestamp alignment failed")
    r5 = btc["btc_log_return_5m"].to_numpy(float)[offsets]
    rv = btc["btc_realized_volatility_240m"].to_numpy(float)[offsets]
    aligned = output["side"].to_numpy(float) * r5 / rv
    output["btc_log_return_5m"] = r5
    output["btc_realized_volatility_240m"] = rv
    output["btc_direction_aligned_impulse"] = aligned
    output["btc_impulse_prior_p75"] = np.nan

    cfg = protocol["single_filter"]
    for _, group in output.groupby(["symbol", "detector_multiplier"], sort=True):
        ordered = group.sort_values(["signal_timestamp_ms", "candidate_id"])
        series = pd.Series(
            ordered["btc_direction_aligned_impulse"].to_numpy(float),
            index=pd.to_datetime(
                ordered["signal_timestamp_ms"].to_numpy(np.int64), unit="ms", utc=True
            ),
        )
        cutoff = series.rolling(
            f"{int(cfg['lookback_calendar_days'])}D",
            closed="left",
            min_periods=int(cfg["minimum_prior_events"]),
        ).quantile(float(cfg["cutoff_percentile"]), interpolation="linear")
        output.loc[ordered.index, "btc_impulse_prior_p75"] = cutoff.to_numpy(float)

    output["btc_feature_ready"] = np.isfinite(output["btc_direction_aligned_impulse"]) & np.isfinite(
        output["btc_impulse_prior_p75"]
    )
    output["btc_filter_pass"] = output["btc_feature_ready"] & (
        output["btc_direction_aligned_impulse"] >= output["btc_impulse_prior_p75"]
    )
    return output.sort_values(
        ["symbol", "detector_multiplier", "signal_timestamp_ms", "candidate_id"]
    ).reset_index(drop=True)


def select_nonoverlap(frame: pd.DataFrame) -> pd.DataFrame:
    selected: list[int] = []
    for _, group in frame.sort_values(
        ["symbol", "entry_timestamp_ms", "candidate_id"]
    ).groupby("symbol", sort=False):
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
    return frame.loc[selected].sort_values(
        ["symbol", "entry_timestamp_ms", "candidate_id"]
    ).copy()


def policy_name(multiplier: float) -> str:
    text = f"{multiplier:g}"
    return f"fixed_k_{text}"


def assign_eval_fold(frame: pd.DataFrame, folds: list[dict[str, str]]) -> pd.DataFrame:
    output = frame.copy()
    output["fold"] = None
    for fold in folds:
        start = int(pd.Timestamp(fold["start"]).timestamp() * 1000)
        end = int(pd.Timestamp(fold["end"]).timestamp() * 1000)
        mask = output["entry_timestamp_ms"].ge(start) & output["exit_timestamp_ms"].lt(end)
        output.loc[mask, "fold"] = fold["name"]
    return output.loc[output["fold"].notna()].copy()


def build_policies(
    featured: pd.DataFrame,
    baseline_file: Path,
    multipliers: list[float],
    folds: list[dict[str, str]],
) -> tuple[dict[float, pd.DataFrame], dict[float, pd.DataFrame], dict[str, Any]]:
    baseline_frozen = pd.read_csv(baseline_file, compression="gzip")
    baseline_frozen = baseline_frozen.loc[baseline_frozen["policy"].str.startswith("fixed_k_")]
    baselines: dict[float, pd.DataFrame] = {}
    filtered: dict[float, pd.DataFrame] = {}
    checks: dict[str, Any] = {}
    for multiplier in multipliers:
        stream = featured.loc[
            np.isclose(featured["detector_multiplier"], multiplier)
        ].copy()
        base_parts = []
        filter_parts = []
        for fold in folds:
            start = int(pd.Timestamp(fold["start"]).timestamp() * 1000)
            end = int(pd.Timestamp(fold["end"]).timestamp() * 1000)
            test = stream.loc[
                stream["entry_timestamp_ms"].ge(start)
                & stream["exit_timestamp_ms"].lt(end)
            ].copy()
            base = select_nonoverlap(test)
            base["fold"] = fold["name"]
            chosen = select_nonoverlap(test.loc[test["btc_filter_pass"]].copy())
            chosen["fold"] = fold["name"]
            base_parts.append(base)
            filter_parts.append(chosen)
        base = pd.concat(base_parts, ignore_index=True)
        chosen = pd.concat(filter_parts, ignore_index=True)
        base["policy"] = policy_name(multiplier)
        chosen["policy"] = f"btc_filtered_k_{multiplier:g}"
        frozen = baseline_frozen.loc[
            np.isclose(baseline_frozen["detector_multiplier"], multiplier)
        ].copy()
        ids_equal = base["candidate_id"].tolist() == frozen["candidate_id"].tolist()
        returns_equal = len(base) == len(frozen) and np.allclose(
            base["net_return_bp"].to_numpy(float),
            frozen["net_return_bp"].to_numpy(float),
            rtol=0,
            atol=1e-12,
        )
        if not ids_equal or not returns_equal:
            raise RuntimeError(f"Phase 5 baseline reproduction failed for k={multiplier}")
        baselines[multiplier] = base
        filtered[multiplier] = chosen
        checks[f"{multiplier:g}"] = {
            "candidate_ids_exact": ids_equal,
            "returns_within_1e_12": returns_equal,
            "trades": len(base),
        }
    return baselines, filtered, checks


def profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    return gains / losses if losses > 0 else None


def metrics(frame: pd.DataFrame) -> dict[str, Any]:
    values = frame["net_return_bp"].to_numpy(float)
    positives = np.sort(values[values > 0])[::-1]
    top_n = max(1, math.ceil(len(values) * 0.05)) if len(values) else 0
    concentration = (
        float(positives[:top_n].sum() / positives.sum()) if positives.size else None
    )
    return {
        "trades": int(len(frame)),
        "unique_weeks": int(frame["week_cluster"].nunique()),
        "mean_net_bp": float(values.mean()) if len(values) else None,
        "total_net_bp": float(values.sum()),
        "profit_factor": profit_factor(values) if len(values) else None,
        "positive_trade_rate": float((values > 0).mean()) if len(values) else None,
        "mean_signal_path_gross_bp": float(frame["signal_path_gross_bp"].mean()) if len(frame) else None,
        "mean_execution_cost_bp": float(frame["execution_cost_bp"].mean()) if len(frame) else None,
        "mean_funding_bp": float(frame["funding_bp"].mean()) if len(frame) else None,
        "tp_first_rate": float(frame["outcome"].eq("tp_first").mean()) if len(frame) else None,
        "sl_first_rate": float(frame["outcome"].eq("sl_first").mean()) if len(frame) else None,
        "timeout_rate": float(frame["outcome"].eq("timeout").mean()) if len(frame) else None,
        "best_5pct_share_positive_pnl": concentration,
    }


def grouped_metrics(frame: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    rows = []
    if frame.empty:
        return rows
    for key, group in frame.groupby(columns, sort=True):
        keys = key if isinstance(key, tuple) else (key,)
        values = group["net_return_bp"].to_numpy(float)
        item = {column: safe(value) for column, value in zip(columns, keys)}
        item.update(
            {
                "trades": int(len(group)),
                "mean_net_bp": float(values.mean()),
                "profit_factor": profit_factor(values),
                "tp_first_rate": float(group["outcome"].eq("tp_first").mean()),
                "sl_first_rate": float(group["outcome"].eq("sl_first").mean()),
                "timeout_rate": float(group["outcome"].eq("timeout").mean()),
            }
        )
        rows.append(item)
    return rows


def weekly_arrays(
    policies: dict[float, pd.DataFrame], weeks: np.ndarray, multipliers: list[float]
) -> tuple[np.ndarray, np.ndarray]:
    sums = np.zeros((len(weeks), len(multipliers)), dtype=float)
    counts = np.zeros_like(sums)
    lookup = {int(week): index for index, week in enumerate(weeks)}
    for column, multiplier in enumerate(multipliers):
        table = policies[multiplier].groupby("week_cluster")["net_return_bp"].agg(["sum", "count"])
        for week, row in table.iterrows():
            position = lookup[int(week)]
            sums[position, column] = float(row["sum"])
            counts[position, column] = float(row["count"])
    return sums, counts


def cluster_ratio_se(sums: np.ndarray, counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    total_count = counts.sum(axis=0)
    means = sums.sum(axis=0) / total_count
    residual = sums - counts * means
    weeks = len(sums)
    se = np.sqrt(weeks / (weeks - 1) * np.square(residual).sum(axis=0)) / total_count
    return means, se


def cluster_difference_se(
    filtered_sums: np.ndarray,
    filtered_counts: np.ndarray,
    baseline_sums: np.ndarray,
    baseline_counts: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    filtered_mean, _ = cluster_ratio_se(filtered_sums, filtered_counts)
    baseline_mean, _ = cluster_ratio_se(baseline_sums, baseline_counts)
    nf = filtered_counts.sum(axis=0)
    nb = baseline_counts.sum(axis=0)
    influence = (
        (filtered_sums - filtered_counts * filtered_mean) / nf
        - (baseline_sums - baseline_counts * baseline_mean) / nb
    )
    weeks = len(filtered_sums)
    se = np.sqrt(weeks / (weeks - 1) * np.square(influence).sum(axis=0))
    return filtered_mean - baseline_mean, se


def bootstrap_max_t(
    filtered_sums: np.ndarray,
    filtered_counts: np.ndarray,
    baseline_sums: np.ndarray,
    baseline_counts: np.ndarray,
    replicates: int,
    block_weeks: int,
    seed: int,
) -> dict[str, Any]:
    weeks, family = filtered_sums.shape
    filtered_mean, filtered_se = cluster_ratio_se(filtered_sums, filtered_counts)
    difference, difference_se = cluster_difference_se(
        filtered_sums, filtered_counts, baseline_sums, baseline_counts
    )
    observed_abs_t = filtered_mean / filtered_se
    observed_diff_t = difference / difference_se
    centered_f = filtered_sums - filtered_counts * filtered_mean
    baseline_mean, _ = cluster_ratio_se(baseline_sums, baseline_counts)
    centered_b = baseline_sums - baseline_counts * baseline_mean

    rng = np.random.default_rng(seed)
    blocks_needed = math.ceil(weeks / block_weeks)
    offsets = np.arange(block_weeks, dtype=np.int64)
    maxima_abs = np.empty(replicates, dtype=float)
    maxima_diff = np.empty(replicates, dtype=float)
    chunk = 200
    for start in range(0, replicates, chunk):
        size = min(chunk, replicates - start)
        starts = rng.integers(0, weeks, size=(size, blocks_needed))
        sampled = ((starts[:, :, None] + offsets) % weeks).reshape(size, -1)[:, :weeks]
        rf = centered_f[sampled]
        cf = filtered_counts[sampled]
        rb = centered_b[sampled]
        cb = baseline_counts[sampled]
        nf = cf.sum(axis=1)
        nb = cb.sum(axis=1)
        mf = rf.sum(axis=1) / nf
        mb = rb.sum(axis=1) / nb
        residual_f = rf - cf * mf[:, None, :]
        se_f = np.sqrt(weeks / (weeks - 1) * np.square(residual_f).sum(axis=1)) / nf
        t_abs = np.divide(mf, se_f, out=np.full_like(mf, -np.inf), where=se_f > 0)
        maxima_abs[start : start + size] = np.max(t_abs, axis=1)

        influence = (
            residual_f / nf[:, None, :]
            - (rb - cb * mb[:, None, :]) / nb[:, None, :]
        )
        se_diff = np.sqrt(weeks / (weeks - 1) * np.square(influence).sum(axis=1))
        diff = mf - mb
        t_diff = np.divide(
            diff, se_diff, out=np.full_like(diff, -np.inf), where=se_diff > 0
        )
        maxima_diff[start : start + size] = np.max(t_diff, axis=1)

    critical_abs = float(np.quantile(maxima_abs, 0.98))
    critical_diff = float(np.quantile(maxima_diff, 0.98))
    abs_lower = filtered_mean - critical_abs * filtered_se
    diff_lower = difference - critical_diff * difference_se
    abs_p = np.array(
        [(1 + np.sum(maxima_abs >= value)) / (replicates + 1) for value in observed_abs_t]
    )
    diff_p = np.array(
        [(1 + np.sum(maxima_diff >= value)) / (replicates + 1) for value in observed_diff_t]
    )
    return {
        "filtered_mean": filtered_mean,
        "filtered_se": filtered_se,
        "absolute_observed_t": observed_abs_t,
        "absolute_critical_98": critical_abs,
        "absolute_simultaneous_lower_98": abs_lower,
        "absolute_familywise_p": abs_p,
        "paired_mean_improvement": difference,
        "paired_se": difference_se,
        "paired_observed_t": observed_diff_t,
        "paired_critical_98": critical_diff,
        "paired_simultaneous_lower_98": diff_lower,
        "paired_familywise_p": diff_p,
        "absolute_max_t_distribution": maxima_abs,
        "paired_max_t_distribution": maxima_diff,
    }


def gate_policy(
    frame: pd.DataFrame,
    summary: dict[str, Any],
    inference: dict[str, float],
    gates: dict[str, Any],
) -> list[dict[str, Any]]:
    by_year = grouped_metrics(frame.assign(year=pd.to_datetime(frame["entry_timestamp_ms"], unit="ms", utc=True).dt.year), ["year"])
    by_half = grouped_metrics(frame, ["halfyear"])
    assets = frame["symbol"].value_counts()
    sides = frame["side"].value_counts()
    rows = [
        {"gate": "minimum_nonoverlap_trades", "value": len(frame), "passed": len(frame) >= int(gates["minimum_nonoverlap_trades"])},
        {"gate": "minimum_unique_calendar_weeks", "value": int(frame["week_cluster"].nunique()), "passed": frame["week_cluster"].nunique() >= int(gates["minimum_unique_calendar_weeks"])},
        {"gate": "minimum_trades_each_target_asset", "value": {symbol: int(assets.get(symbol, 0)) for symbol in ("ETHUSDT", "XRPUSDT")}, "passed": min(int(assets.get(symbol, 0)) for symbol in ("ETHUSDT", "XRPUSDT")) >= int(gates["minimum_trades_each_target_asset"])},
        {"gate": "minimum_trades_each_side", "value": {"long": int(sides.get(1, 0)), "short": int(sides.get(-1, 0))}, "passed": min(int(sides.get(side, 0)) for side in (-1, 1)) >= int(gates["minimum_trades_each_side"])},
        {"gate": "minimum_profit_factor", "value": summary["profit_factor"], "passed": summary["profit_factor"] is not None and summary["profit_factor"] >= float(gates["minimum_profit_factor"])},
        {"gate": "both_calendar_years_positive", "value": by_year, "passed": len(by_year) == 2 and all(item["mean_net_bp"] > 0 for item in by_year)},
        {"gate": "minimum_positive_halfyears", "value": by_half, "passed": sum(item["mean_net_bp"] > 0 for item in by_half) >= int(gates["minimum_positive_halfyears_out_of_four"])},
        {"gate": "absolute_simultaneous_98_lower_above_zero", "value": inference["absolute_lower"], "passed": inference["absolute_lower"] > float(gates["absolute_simultaneous_98_lower_bound_above_bp"])},
        {"gate": "paired_simultaneous_98_lower_above_zero", "value": inference["paired_lower"], "passed": inference["paired_lower"] > float(gates["paired_improvement_simultaneous_98_lower_bound_above_bp"])},
        {"gate": "additional_2bp_stress_positive", "value": summary["mean_net_bp"] - float(gates["additional_cost_stress_bp"]), "passed": summary["mean_net_bp"] - float(gates["additional_cost_stress_bp"]) > 0},
        {"gate": "winner_concentration", "value": summary["best_5pct_share_positive_pnl"], "passed": summary["best_5pct_share_positive_pnl"] is not None and summary["best_5pct_share_positive_pnl"] <= float(gates["top_5_percent_winners_max_share_of_positive_pnl"])},
    ]
    return rows


def self_test() -> None:
    frame = pd.DataFrame(
        {
            "candidate_id": [1, 2, 3],
            "symbol": ["ETHUSDT"] * 3,
            "entry_timestamp_ms": [0, 60_000, 180_000],
            "busy_until_ms": [120_000, 180_000, 240_000],
        }
    )
    selected = select_nonoverlap(frame)
    if selected["candidate_id"].tolist() != [1, 3]:
        raise RuntimeError("Non-overlap self-test failed")
    sums = np.array([[1.0, 2.0], [-1.0, 1.0], [2.0, -1.0], [1.0, 2.0]])
    counts = np.ones_like(sums)
    baseline_sums = sums - np.array(
        [[0.2, 0.1], [0.1, 0.3], [0.4, 0.2], [0.2, 0.4]]
    )
    out = bootstrap_max_t(sums, counts, baseline_sums, counts, 200, 2, 123)
    if len(out["absolute_max_t_distribution"]) != 200:
        raise RuntimeError("Bootstrap self-test failed")
    print("H2 synthetic self-tests: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--phase5-config", type=Path)
    parser.add_argument("--candidate-database", type=Path)
    parser.add_argument("--candidate-archive", type=Path)
    parser.add_argument("--baseline-trades", type=Path)
    parser.add_argument("--btc-data-dir", type=Path)
    parser.add_argument("--btc-source-manifest", type=Path)
    parser.add_argument("--btc-source-audit", type=Path)
    parser.add_argument("--btc-ledger-audit", type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    required = [
        "protocol", "phase5_config", "candidate_database", "candidate_archive", "baseline_trades",
        "btc_data_dir", "btc_source_manifest", "btc_source_audit",
        "btc_ledger_audit", "output_dir",
    ]
    if any(getattr(args, name) is None for name in required):
        raise SystemExit("All input and output paths are required unless --self-test is used")

    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    if protocol.get("status") != "frozen_before_BTC_data_or_outcomes":
        raise RuntimeError("H2 protocol does not carry the required frozen status")
    hashes = verify_inputs(args)
    database_check = verify_database(args.candidate_database, args.candidate_archive)
    multipliers = [float(value) for value in protocol["detector_family"]["multipliers"]]
    candidates = load_candidates(args.candidate_database)
    print(f"Loaded {len(candidates):,} frozen ETH/XRP candidate events", flush=True)
    btc = load_btc(args.btc_data_dir, args.btc_source_manifest)
    print(f"Loaded {len(btc):,} verified BTC one-minute candles", flush=True)
    featured = add_btc_feature(candidates, btc, protocol)
    baselines, filtered, baseline_checks = build_policies(
        featured,
        args.baseline_trades,
        multipliers,
        json.loads(args.phase5_config.read_text(encoding="utf-8"))["walk_forward_folds"],
    )
    print("All nine frozen fixed-k baselines reproduced exactly", flush=True)

    all_base = pd.concat(baselines.values(), ignore_index=True)
    all_filtered = pd.concat(filtered.values(), ignore_index=True)
    week_min = int(all_base["week_cluster"].min())
    week_max = int(all_base["week_cluster"].max())
    weeks = np.arange(week_min, week_max + 1, dtype=np.int64)
    filtered_sums, filtered_counts = weekly_arrays(filtered, weeks, multipliers)
    baseline_sums, baseline_counts = weekly_arrays(baselines, weeks, multipliers)
    infer_cfg = protocol["inference"]
    inference = bootstrap_max_t(
        filtered_sums,
        filtered_counts,
        baseline_sums,
        baseline_counts,
        int(infer_cfg["replicates"]),
        int(infer_cfg["block_weeks"]),
        int(infer_cfg["random_seed"]),
    )

    policies = []
    for index, multiplier in enumerate(multipliers):
        chosen = filtered[multiplier].copy()
        chosen["year"] = pd.to_datetime(
            chosen["entry_timestamp_ms"], unit="ms", utc=True
        ).dt.year
        filtered_summary = metrics(chosen)
        baseline_summary = metrics(baselines[multiplier])
        infer_values = {
            "cluster_se_bp": float(inference["filtered_se"][index]),
            "absolute_observed_t": float(inference["absolute_observed_t"][index]),
            "absolute_familywise_p": float(inference["absolute_familywise_p"][index]),
            "absolute_lower": float(inference["absolute_simultaneous_lower_98"][index]),
            "paired_mean_improvement": float(inference["paired_mean_improvement"][index]),
            "paired_se_bp": float(inference["paired_se"][index]),
            "paired_observed_t": float(inference["paired_observed_t"][index]),
            "paired_familywise_p": float(inference["paired_familywise_p"][index]),
            "paired_lower": float(inference["paired_simultaneous_lower_98"][index]),
        }
        gates = gate_policy(
            chosen, filtered_summary, infer_values, protocol["required_evidence_gates"]
        )
        policies.append(
            {
                "detector_multiplier": multiplier,
                "filter_events_before_nonoverlap": int(
                    featured.loc[
                        np.isclose(featured["detector_multiplier"], multiplier)
                        & featured["entry_timestamp_ms"].ge(EVAL_START_MS)
                        & featured["exit_timestamp_ms"].lt(EVAL_END_MS),
                        "btc_filter_pass",
                    ].sum()
                ),
                "baseline": baseline_summary,
                "filtered": filtered_summary,
                "inference": infer_values,
                "by_fold": grouped_metrics(chosen, ["fold"]),
                "by_year": grouped_metrics(chosen, ["year"]),
                "by_symbol_side": grouped_metrics(chosen, ["symbol", "side"]),
                "gates": gates,
                "all_gates_pass": all(item["passed"] for item in gates),
            }
        )

    passing = [item for item in policies if item["all_gates_pass"]]
    passing.sort(
        key=lambda item: (
            -item["inference"]["absolute_lower"], item["detector_multiplier"]
        )
    )
    decision = "PASS_FREEZE_ONE_CANDIDATE" if passing else "FAIL_REJECT_H2_NO_NEARBY_BTC_TESTS"
    selected_candidate = passing[0]["detector_multiplier"] if passing else None
    results = {
        "study": protocol["name"],
        "decision": decision,
        "selected_candidate_multiplier": selected_candidate,
        "protocol_sha256": hashes["protocol"],
        "input_hashes": hashes,
        "database_check": database_check,
        "btc_rows_read": len(btc),
        "target_candidate_rows_read": len(candidates),
        "locked_2025_2026_rows_read": 0,
        "feature_specification": protocol["single_BTC_feature"],
        "filter_specification": protocol["single_filter"],
        "quantile_implementation": "pandas rolling 180D, closed=left, min_periods=50, q=0.75, linear interpolation",
        "baseline_reproduction": baseline_checks,
        "inference": {
            "weeks": len(weeks),
            "week_cluster_definition": "floor(entry_timestamp_ms / 604800000), inherited from Phase 5",
            "replicates": int(infer_cfg["replicates"]),
            "block_weeks": int(infer_cfg["block_weeks"]),
            "circular_blocks": True,
            "simultaneous_one_sided_confidence": float(infer_cfg["simultaneous_one_sided_confidence"]),
            "family_size": len(multipliers),
            "seed": int(infer_cfg["random_seed"]),
            "absolute_max_t_critical_98": float(inference["absolute_critical_98"]),
            "paired_max_t_critical_98": float(inference["paired_critical_98"]),
        },
        "policies": policies,
        "program_stopping_rule_applied": not passing,
    }

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "H2_BTC_LEAD_LAG_RESULTS.json"
    event_path = output / "h2_btc_event_audit.jsonl.gz"
    filtered_path = output / "h2_btc_filtered_trades.jsonl.gz"
    baseline_path = output / "h2_btc_matched_baselines.jsonl.gz"
    distribution_path = output / "H2_MAXT_DISTRIBUTIONS.json.gz"
    write_json(result_path, results)
    event_columns = [
        "candidate_id", "symbol", "detector_multiplier", "side",
        "signal_timestamp_ms", "entry_timestamp_ms", "exit_timestamp_ms",
        "busy_until_ms", "outcome", "net_return_bp", "btc_log_return_5m",
        "btc_realized_volatility_240m", "btc_direction_aligned_impulse",
        "btc_impulse_prior_p75", "btc_feature_ready", "btc_filter_pass",
    ]
    deterministic_jsonl_gzip(featured[event_columns], event_path)
    trade_columns = list(all_filtered.columns)
    deterministic_jsonl_gzip(all_filtered[trade_columns], filtered_path)
    deterministic_jsonl_gzip(all_base[list(all_base.columns)], baseline_path)
    deterministic_json_gzip(
        {
            "absolute_max_t": inference["absolute_max_t_distribution"].tolist(),
            "paired_max_t": inference["paired_max_t_distribution"].tolist(),
        },
        distribution_path,
    )
    artifacts = {}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "H2_OUTPUT_MANIFEST.json":
            artifacts[path.name] = {"sha256": sha256(path), "size_bytes": path.stat().st_size}
    write_json(
        output / "H2_OUTPUT_MANIFEST.json",
        {
            "decision": decision,
            "protocol_sha256": hashes["protocol"],
            "candidate_archive_sha256": hashes["candidate_archive"],
            "baseline_trades_sha256": hashes["baseline_trades"],
            "artifacts": artifacts,
            "manifest_self_hash_excluded": True,
        },
    )
    print(json.dumps({"decision": decision, "passing_policies": len(passing)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
