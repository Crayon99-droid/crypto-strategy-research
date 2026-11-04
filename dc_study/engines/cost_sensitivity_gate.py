#!/usr/bin/env python3
"""Cost-only sensitivity analysis for the frozen Phase 5 fixed-k stream."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "COST_SENSITIVITY_PROTOCOL.json"
OUTPUT_PATH = HERE / "COST_SENSITIVITY_RESULTS.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    return gains / losses if losses > 0 else None


def moving_block_indices(n_weeks: int, block_weeks: int, replicates: int, seed: int) -> np.ndarray:
    if n_weeks < block_weeks:
        raise ValueError("Fewer weeks than the frozen block length")
    starts = np.arange(n_weeks - block_weeks + 1, dtype=np.int32)
    blocks_needed = math.ceil(n_weeks / block_weeks)
    rng = np.random.default_rng(seed)
    sampled = rng.choice(starts, size=(replicates, blocks_needed), replace=True)
    offsets = np.arange(block_weeks, dtype=np.int32)
    return (sampled[:, :, None] + offsets).reshape(replicates, -1)[:, :n_weeks]


def bootstrap_means(frame: pd.DataFrame, value_column: str, sampled_weeks: np.ndarray) -> np.ndarray:
    weekly = (
        frame.groupby("week_cluster", sort=True)[value_column]
        .agg(["sum", "count"])
        .reset_index(drop=True)
    )
    if len(weekly) != sampled_weeks.shape[1]:
        raise RuntimeError("Weekly bootstrap shape mismatch")
    sums = weekly["sum"].to_numpy(float)[sampled_weeks].sum(axis=1)
    counts = weekly["count"].to_numpy(float)[sampled_weeks].sum(axis=1)
    return sums / counts


def summary(values: np.ndarray, bootstrap: np.ndarray) -> dict[str, Any]:
    return {
        "trades": int(values.size),
        "mean_net_bp": float(values.mean()),
        "total_net_bp": float(values.sum()),
        "profit_factor": profit_factor(values),
        "positive_trade_rate": float((values > 0).mean()),
        "bootstrap_lower_95_bp": float(np.quantile(bootstrap, 0.025)),
        "bootstrap_upper_95_bp": float(np.quantile(bootstrap, 0.975)),
        "bootstrap_lower_98_one_sided_bp": float(np.quantile(bootstrap, 0.02)),
    }


def grouped_summary(frame: pd.DataFrame, value_column: str, group_columns: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for keys, group in frame.groupby(group_columns, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        values = group[value_column].to_numpy(float)
        item = {
            column: key.item() if isinstance(key, np.generic) else key
            for column, key in zip(group_columns, keys)
        }
        item.update({
            "trades": int(values.size),
            "mean_net_bp": float(values.mean()),
            "profit_factor": profit_factor(values),
            "positive_trade_rate": float((values > 0).mean()),
        })
        records.append(item)
    return records


def main() -> None:
    protocol = json.loads(PROTOCOL_PATH.read_text())
    if not protocol.get("frozen_before_cost_gate_outcomes"):
        raise RuntimeError("Protocol is not frozen")
    input_path = ROOT / protocol["input"]["file"]
    actual_hash = sha256(input_path)
    if actual_hash != protocol["input"]["expected_sha256"]:
        raise RuntimeError("Frozen trade input hash mismatch")

    trades = pd.read_csv(input_path)
    trades = trades[trades["policy"].eq(protocol["input"]["policy"])].copy()
    if len(trades) != int(protocol["input"]["expected_trade_rows"]):
        raise RuntimeError("Frozen policy row count mismatch")
    locked_start = int(pd.Timestamp("2025-01-01T00:00:00Z").timestamp() * 1000)
    if int(trades["entry_timestamp_ms"].max()) >= locked_start:
        raise RuntimeError("Locked 2025+ trade row encountered")
    if set(trades["symbol"]) != {"ETHUSDT", "XRPUSDT"}:
        raise RuntimeError("Unexpected instrument in cost gate")

    inference = protocol["inference"]
    weeks = np.sort(trades["week_cluster"].unique())
    if not np.array_equal(weeks, np.arange(weeks.min(), weeks.max() + 1)):
        raise RuntimeError("Calendar-week sequence contains a gap")
    sampled_weeks = moving_block_indices(
        len(weeks),
        int(inference["block_weeks"]),
        int(inference["replicates"]),
        int(inference["random_seed"]),
    )

    scenarios: list[dict[str, Any]] = []
    scenario_columns: list[str] = []
    for order in protocol["scenario_order_types"]:
        name = str(order["name"])
        entry_fee = float(order["entry_fee_bp"])
        exit_fee = float(order["exit_fee_bp"])

        ideal_fee = entry_fee + exit_fee * trades["exit_reference_price"] / trades["entry_open"]
        ideal_column = f"ideal_fee_only_{name}_bp"
        trades[ideal_column] = trades["signal_path_gross_bp"] + trades["funding_bp"] - ideal_fee
        ideal_boot = bootstrap_means(trades, ideal_column, sampled_weeks)
        item = {
            "scenario": f"ideal_fee_only_{name}",
            "family": "fee_only_idealized",
            "entry_fee_bp": entry_fee,
            "exit_fee_bp": exit_fee,
            "nonfee_drag": "zero",
            "maker_fill_claim": False,
        }
        item.update(summary(trades[ideal_column].to_numpy(float), ideal_boot))
        scenarios.append(item)
        scenario_columns.append(ideal_column)

        observed_fee = entry_fee + exit_fee * trades["exit_fill_price"] / trades["entry_price"]
        observed_column = f"same_fill_{name}_bp"
        trades[observed_column] = trades["gross_after_slippage_bp"] + trades["funding_bp"] - observed_fee
        observed_boot = bootstrap_means(trades, observed_column, sampled_weeks)
        item = {
            "scenario": f"same_fill_{name}",
            "family": "same_observed_fills_fee_substitution",
            "entry_fee_bp": entry_fee,
            "exit_fee_bp": exit_fee,
            "nonfee_drag": "Phase 5 observed fill geometry",
            "maker_fill_claim": False,
        }
        item.update(summary(trades[observed_column].to_numpy(float), observed_boot))
        scenarios.append(item)
        scenario_columns.append(observed_column)

    # The account taker/taker, same-fill scenario must reproduce Phase 5 exactly.
    reproduction_error = float(np.max(np.abs(trades["same_fill_taker_taker_bp"] - trades["net_return_bp"])))
    if reproduction_error > 1e-9:
        raise RuntimeError("Taker/taker scenario failed to reproduce Phase 5")

    zero_column = "zero_execution_cost_net_bp"
    zero_values = trades[zero_column].to_numpy(float)
    zero_boot = bootstrap_means(trades, zero_column, sampled_weeks)
    grid_cfg = protocol["scenario_families"]["all_in_cost_surface"]
    costs = np.arange(
        float(grid_cfg["start_bp"]),
        float(grid_cfg["end_bp"]) + float(grid_cfg["step_bp"]) / 2.0,
        float(grid_cfg["step_bp"]),
    )
    cost_surface = []
    for cost in costs:
        values = zero_values - cost
        cost_surface.append({
            "all_in_cost_bp": float(cost),
            "mean_net_bp": float(values.mean()),
            "profit_factor": profit_factor(values),
            "positive_trade_rate": float((values > 0).mean()),
            "bootstrap_lower_95_bp": float(np.quantile(zero_boot - cost, 0.025)),
            "bootstrap_upper_95_bp": float(np.quantile(zero_boot - cost, 0.975)),
            "bootstrap_lower_98_one_sided_bp": float(np.quantile(zero_boot - cost, 0.02)),
        })

    fold_breakdowns = []
    symbol_side_breakdowns = []
    for column in scenario_columns:
        name = column.removesuffix("_bp")
        fold = grouped_summary(trades, column, ["halfyear"])
        for item in fold:
            item["scenario_column"] = name
        fold_breakdowns.extend(fold)
        detail = grouped_summary(trades, column, ["symbol", "side"])
        for item in detail:
            item["scenario_column"] = name
        symbol_side_breakdowns.extend(detail)

    scenario_lookup = {item["scenario"]: item for item in scenarios}
    safety_stress = float(protocol["decision_logic"]["safety_stress_bp"])
    current = scenario_lookup["same_fill_taker_taker"]
    ideal_mm = scenario_lookup["ideal_fee_only_maker_maker"]
    same_mm = scenario_lookup["same_fill_maker_maker"]
    executable_pass = bool(
        current["mean_net_bp"] > 0 and current["bootstrap_lower_98_one_sided_bp"] > 0
    )
    maker_bound_positive = bool(ideal_mm["mean_net_bp"] > 0)
    maker_bound_statistically_positive = bool(ideal_mm["bootstrap_lower_98_one_sided_bp"] > 0)
    payload = {
        "protocol": protocol,
        "integrity": {
            "input_sha256": actual_hash,
            "input_hash_match": True,
            "policy_trade_rows": len(trades),
            "calendar_weeks": int(len(weeks)),
            "halfyears": sorted(trades["halfyear"].unique().tolist()),
            "symbols": sorted(trades["symbol"].unique().tolist()),
            "locked_2025_2026_rows_read": 0,
            "taker_taker_reproduction_max_abs_error_bp": reproduction_error,
        },
        "edge_budget": {
            "mean_zero_execution_cost_return_bp": float(zero_values.mean()),
            "mean_break_even_all_in_cost_bp": float(zero_values.mean()),
            "bootstrap_lower_95_cost_capacity_bp": float(np.quantile(zero_boot, 0.025)),
            "bootstrap_lower_98_one_sided_cost_capacity_bp": float(np.quantile(zero_boot, 0.02)),
            "current_mean_execution_drag_bp": float(trades["execution_cost_bp"].mean()),
            "current_mean_fee_bp": float(trades["fee_bp"].mean()),
            "current_mean_nonfee_drag_bp": float((trades["execution_cost_bp"] - trades["fee_bp"]).mean()),
        },
        "scenarios": scenarios,
        "cost_surface": cost_surface,
        "fold_breakdowns": fold_breakdowns,
        "symbol_side_breakdowns": symbol_side_breakdowns,
        "decision": {
            "unconditional_taker_taker_pass": executable_pass,
            "idealized_maker_maker_mean_positive": maker_bound_positive,
            "idealized_maker_maker_98_lower_bound_positive": maker_bound_statistically_positive,
            "same_fill_maker_maker_mean_net_bp": same_mm["mean_net_bp"],
            "current_taker_taker_plus_2bp_stress_mean_bp": current["mean_net_bp"] - safety_stress,
            "next_action": "RUN_PREREGISTERED_CONFIRMATION_IMPULSE_SELECTIVITY",
            "maker_interpretation": "Mathematical fee bounds only; authentic maker-fill feasibility remains untested.",
        },
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({
        "edge_budget": payload["edge_budget"],
        "scenario_summary": scenarios,
        "decision": payload["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
