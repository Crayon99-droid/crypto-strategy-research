#!/usr/bin/env python3
"""Diagnostic-only amendment for the frozen Phase 4 checkpoint.

This script does not alter regimes, thresholds, trade selection, or exits.  It
decomposes the already-frozen trades and training cells by exit reason, time to
resolution, and execution-cost scenario.
"""

from __future__ import annotations

import importlib.util
import gzip
import json
import math
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
PHASE4 = ROOT.parent / "phase4"
RESULTS = PHASE4 / "results"
OUTPUT = ROOT / "results"
OUTPUT.mkdir(parents=True, exist_ok=True)


def load_engine():
    path = PHASE4 / "phase4_regime_normalized_dc.py"
    spec = importlib.util.spec_from_file_location("phase4_engine", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def profit_factor(values: pd.Series | np.ndarray) -> float | None:
    array = np.asarray(values, dtype=float)
    gains = array[array > 0].sum()
    losses = -array[array < 0].sum()
    return float(gains / losses) if losses > 0 else None


def add_diagnostic_returns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["exit_reference_price"] = frame["entry_price"] * (
        1.0 + frame["side"] * frame["gross_reference_bp"] / 10_000.0
    )
    frame["signal_path_gross_bp"] = frame["side"] * (
        frame["exit_reference_price"] / frame["entry_open"] - 1.0
    ) * 10_000.0
    frame["slippage_and_tick_cost_bp"] = (
        frame["signal_path_gross_bp"] - frame["gross_after_slippage_bp"]
    )
    frame["execution_cost_bp"] = frame["slippage_and_tick_cost_bp"] + frame["fee_bp"]
    frame["half_execution_cost_net_bp"] = (
        frame["signal_path_gross_bp"] - 0.5 * frame["execution_cost_bp"] + frame["funding_bp"]
    )
    frame["zero_execution_cost_net_bp"] = frame["signal_path_gross_bp"] + frame["funding_bp"]
    frame["zero_all_cost_signal_bp"] = frame["signal_path_gross_bp"]
    return frame


def outcome_metrics(group: pd.DataFrame) -> dict[str, Any]:
    n = len(group)
    counts = group["outcome"].value_counts()
    output: dict[str, Any] = {
        "trades": n,
        "tp_first": int(counts.get("tp_first", 0)),
        "sl_first": int(counts.get("sl_first", 0)),
        "timeout": int(counts.get("timeout", 0)),
        "tp_first_rate": float((group["outcome"] == "tp_first").mean()) if n else None,
        "sl_first_rate": float((group["outcome"] == "sl_first").mean()) if n else None,
        "timeout_rate": float((group["outcome"] == "timeout").mean()) if n else None,
        "ambiguous_same_minute": int(group["ambiguous_same_minute"].sum()) if n else 0,
        "median_exit_minutes": float(group["exit_minutes"].median()) if n else None,
        "mean_full_cost_net_bp": float(group["net_return_bp"].mean()) if n else None,
        "full_cost_profit_factor": profit_factor(group["net_return_bp"]) if n else None,
        "mean_signal_path_gross_bp": float(group["signal_path_gross_bp"].mean()) if n else None,
        "signal_path_profit_factor": profit_factor(group["signal_path_gross_bp"]) if n else None,
        "mean_slippage_and_tick_cost_bp": float(group["slippage_and_tick_cost_bp"].mean()) if n else None,
        "mean_fee_bp": float(group["fee_bp"].mean()) if n else None,
        "mean_funding_bp": float(group["funding_bp"].mean()) if n else None,
        "mean_half_execution_cost_net_bp": float(group["half_execution_cost_net_bp"].mean()) if n else None,
        "half_execution_cost_profit_factor": profit_factor(group["half_execution_cost_net_bp"]) if n else None,
        "mean_zero_execution_cost_net_bp": float(group["zero_execution_cost_net_bp"].mean()) if n else None,
        "zero_execution_cost_profit_factor": profit_factor(group["zero_execution_cost_net_bp"]) if n else None,
    }
    for outcome in ("tp_first", "sl_first", "timeout"):
        subset = group.loc[group["outcome"] == outcome]
        output[f"{outcome}_mean_full_net_bp"] = float(subset["net_return_bp"].mean()) if len(subset) else None
        output[f"{outcome}_mean_signal_gross_bp"] = float(subset["signal_path_gross_bp"].mean()) if len(subset) else None
        output[f"{outcome}_median_minutes"] = float(subset["exit_minutes"].median()) if len(subset) else None
    return output


def grouped(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    rows = []
    for key, group in frame.groupby(columns, sort=True, observed=True):
        key = key if isinstance(key, tuple) else (key,)
        rows.append({**dict(zip(columns, key)), **outcome_metrics(group)})
    return pd.DataFrame(rows)


def detailed_outcome(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    rows = []
    for key, group in frame.groupby(columns + ["outcome"], sort=True, observed=True):
        key = key if isinstance(key, tuple) else (key,)
        identity = dict(zip(columns + ["outcome"], key))
        rows.append(
            {
                **identity,
                "trades": len(group),
                "mean_exit_minutes": float(group["exit_minutes"].mean()),
                "median_exit_minutes": float(group["exit_minutes"].median()),
                "mean_signal_path_gross_bp": float(group["signal_path_gross_bp"].mean()),
                "mean_full_cost_net_bp": float(group["net_return_bp"].mean()),
                "mean_slippage_and_tick_cost_bp": float(group["slippage_and_tick_cost_bp"].mean()),
                "mean_fee_bp": float(group["fee_bp"].mean()),
                "mean_funding_bp": float(group["funding_bp"].mean()),
            }
        )
    return pd.DataFrame(rows)


def resolution_table(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    horizons = [15, 30, 60, 120, 240]
    rows = []
    for key, group in frame.groupby(columns, sort=True, observed=True):
        key = key if isinstance(key, tuple) else (key,)
        identity = dict(zip(columns, key))
        for horizon in horizons:
            tp = int(((group["outcome"] == "tp_first") & (group["exit_minutes"] <= horizon)).sum())
            sl = int(((group["outcome"] == "sl_first") & (group["exit_minutes"] <= horizon)).sum())
            unresolved = len(group) - tp - sl
            rows.append(
                {
                    **identity,
                    "horizon_minutes": horizon,
                    "trades": len(group),
                    "tp_resolved": tp,
                    "sl_resolved": sl,
                    "unresolved": unresolved,
                    "tp_resolved_rate": tp / len(group),
                    "sl_resolved_rate": sl / len(group),
                    "unresolved_rate": unresolved / len(group),
                }
            )
    return pd.DataFrame(rows)


def training_cell_breakdown(config: dict[str, Any]) -> pd.DataFrame:
    engine = load_engine()
    with tempfile.TemporaryDirectory(prefix="phase4-1-") as directory:
        database = Path(directory) / "candidates.sqlite"
        with gzip.open(PHASE4 / "phase4_candidates_2021_2024.sqlite.gz", "rb") as source, database.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        connection = sqlite3.connect(database)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Verified compressed candidate database failed decompression integrity")
            candidates = pd.read_sql_query("SELECT * FROM candidates", connection)
        finally:
            connection.close()
    candidates = add_diagnostic_returns(candidates)
    rows = []
    for fold in config["walk_forward_folds"]:
        start = int(pd.Timestamp(fold["start"]).timestamp() * 1000)
        training = candidates.loc[candidates["exit_timestamp_ms"] < start]
        for multiplier in config["threshold_multipliers"]:
            stream = training.loc[np.isclose(training["detector_multiplier"], float(multiplier))]
            economic = engine.select_nonoverlap(stream) if len(stream) else stream.copy()
            for regime in config["regimes"]:
                cell = economic.loc[economic["regime"] == regime]
                rows.append(
                    {
                        "fold": fold["name"],
                        "regime": regime,
                        "detector_multiplier": float(multiplier),
                        **outcome_metrics(cell),
                    }
                )
    return pd.DataFrame(rows)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, allow_nan=False, default=lambda value: value.item() if isinstance(value, np.generic) else value) + "\n",
        encoding="utf-8",
    )


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output = []
    for row in frame.to_dict(orient="records"):
        output.append({key: None if pd.isna(value) else value.item() if isinstance(value, np.generic) else value for key, value in row.items()})
    return output


def main() -> None:
    config = json.loads((PHASE4 / "phase4_config.json").read_text(encoding="utf-8"))
    prereg = json.loads((ROOT / "PHASE4_1_PREREGISTRATION.json").read_text(encoding="utf-8"))
    trades = pd.read_csv(RESULTS / "phase4_walkforward_trades.csv.gz")
    trades = add_diagnostic_returns(trades)

    training = training_cell_breakdown(config)
    training.to_csv(OUTPUT / "phase4_1_training_cell_exit_breakdown.csv", index=False)

    policy_fold = grouped(trades, ["policy", "fold"])
    policy_fold.to_csv(OUTPUT / "phase4_1_policy_fold_exit_cost_summary.csv", index=False)
    policy_cell = grouped(trades, ["policy", "fold", "regime", "detector_multiplier"])
    policy_cell.to_csv(OUTPUT / "phase4_1_walkforward_cell_exit_cost_summary.csv", index=False)
    side = grouped(trades, ["policy", "fold", "side"])
    side.to_csv(OUTPUT / "phase4_1_policy_fold_side_exit_cost_summary.csv", index=False)
    detailed = detailed_outcome(
        trades,
        ["policy", "fold", "regime", "detector_multiplier", "symbol", "side"],
    )
    detailed.to_csv(OUTPUT / "phase4_1_detailed_exit_reason_breakdown.csv", index=False)
    resolution = resolution_table(trades, ["policy", "fold"])
    resolution.to_csv(OUTPUT / "phase4_1_time_to_resolution.csv", index=False)

    audit_fold = trades.loc[(trades["fold"] == "2024 H1") & (trades["policy"] == "regime_forced")].copy()
    audit_fold.to_csv(OUTPUT / "phase4_2024H1_forced_regime_trade_level.csv", index=False)
    trades.loc[trades["fold"] == "2024 H1"].to_csv(
        OUTPUT / "phase4_2024H1_all_policy_trade_level.csv.gz",
        index=False,
        compression={"method": "gzip", "compresslevel": 6, "mtime": 0},
    )

    fixed = policy_fold.loc[policy_fold["policy"] == "fixed_k_1.25"].copy()
    positive_folds = int((fixed["mean_signal_path_gross_bp"] > 0).sum())
    fixed_trades = trades.loc[trades["policy"] == "fixed_k_1.25"]
    aggregate_signal_mean = float(fixed_trades["signal_path_gross_bp"].mean())
    aggregate_signal_pf = profit_factor(fixed_trades["signal_path_gross_bp"])
    gate = prereg["phase5_permission_gate"]
    phase5_permitted = bool(
        positive_folds >= int(gate["required_positive_folds"])
        and aggregate_signal_mean > 0
        and aggregate_signal_pf is not None
        and aggregate_signal_pf > float(gate["aggregate_profit_factor_must_exceed"])
    )
    forced = trades.loc[trades["policy"] == "regime_forced"]
    outcome_forced = outcome_metrics(forced)
    outcome_fixed = outcome_metrics(fixed_trades)
    summary = {
        "name": "Phase 4.1 Diagnostic Amendment",
        "diagnostic_only": True,
        "trade_selection_changed": False,
        "phase4_trade_rows_read": len(trades),
        "training_cells_decomposed": len(training),
        "forced_regime_policy": outcome_forced,
        "fixed_k_1_25": {
            **outcome_fixed,
            "fold_signal_path_results": records(
                fixed[[
                    "fold", "trades", "mean_signal_path_gross_bp", "signal_path_profit_factor",
                    "mean_full_cost_net_bp", "full_cost_profit_factor", "tp_first_rate",
                    "sl_first_rate", "timeout_rate",
                ]]
            ),
            "positive_signal_path_folds": positive_folds,
            "aggregate_signal_path_mean_bp": aggregate_signal_mean,
            "aggregate_signal_path_profit_factor": aggregate_signal_pf,
        },
        "phase5_permission_gate": {
            "required_positive_signal_path_folds": int(gate["required_positive_folds"]),
            "observed_positive_signal_path_folds": positive_folds,
            "aggregate_signal_path_mean_must_be_positive": True,
            "observed_aggregate_signal_path_mean_bp": aggregate_signal_mean,
            "aggregate_signal_path_pf_must_exceed": float(gate["aggregate_profit_factor_must_exceed"]),
            "observed_aggregate_signal_path_pf": aggregate_signal_pf,
            "phase5_permitted": phase5_permitted,
            "decision": "Permit exactly one final preregistered Phase 5" if phase5_permitted else "Terminate the directional-change reversal research line",
        },
        "holding_period_statement": {
            "phase4_tested_minutes": 240,
            "other_holds_tested_as_causal_policies": False,
            "reason": "Changing the hold changes exit prices, non-overlap, and subsequent trade eligibility; the existing trade table cannot establish a causal alternative-hold result.",
            "time_to_resolution_table_is_descriptive_only": True,
        },
        "conclusion_scope": "No regime-threshold cell was tradable under the frozen 1:1 extreme-anchored, 240-minute, full-cost Phase 4 rule. This is not a general rejection of every exit rule.",
        "locked_2025_2026_price_rows_read": 0,
    }
    write_json(OUTPUT / "PHASE4_1_DIAGNOSTIC_RESULTS.json", summary)
    print(json.dumps(summary["phase5_permission_gate"], indent=2))


if __name__ == "__main__":
    main()
