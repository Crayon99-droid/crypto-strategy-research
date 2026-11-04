#!/usr/bin/env python3
"""Create diagnostic-only Phase 5 summaries from frozen walk-forward trades."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
PHASE4_1 = ROOT.parent / "phase4_1" / "results" / "PHASE4_1_DIAGNOSTIC_RESULTS.json"


def profit_factor(values: pd.Series) -> float | None:
    gains = float(values.loc[values > 0].sum())
    losses = float(-values.loc[values < 0].sum())
    return gains / losses if losses > 0 else None


def summarize(group: pd.DataFrame) -> dict[str, float | int | None]:
    n = len(group)
    return {
        "trades": n,
        "tp_first": int(group["outcome"].eq("tp_first").sum()),
        "sl_first": int(group["outcome"].eq("sl_first").sum()),
        "timeout": int(group["outcome"].eq("timeout").sum()),
        "tp_first_rate": float(group["outcome"].eq("tp_first").mean()) if n else None,
        "sl_first_rate": float(group["outcome"].eq("sl_first").mean()) if n else None,
        "timeout_rate": float(group["outcome"].eq("timeout").mean()) if n else None,
        "median_exit_minutes": float(group["exit_minutes"].median()) if n else None,
        "mean_signal_path_gross_bp": float(group["signal_path_gross_bp"].mean()) if n else None,
        "signal_path_profit_factor": profit_factor(group["signal_path_gross_bp"]) if n else None,
        "mean_half_execution_cost_net_bp": float(group["half_execution_cost_net_bp"].mean()) if n else None,
        "half_execution_cost_profit_factor": profit_factor(group["half_execution_cost_net_bp"]) if n else None,
        "mean_full_cost_net_bp": float(group["net_return_bp"].mean()) if n else None,
        "full_cost_profit_factor": profit_factor(group["net_return_bp"]) if n else None,
        "mean_execution_cost_bp": float(group["execution_cost_bp"].mean()) if n else None,
        "mean_fee_bp": float(group["fee_bp"].mean()) if n else None,
        "mean_funding_bp": float(group["funding_bp"].mean()) if n else None,
    }


def grouped(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    rows: list[dict] = []
    for key, group in frame.groupby(columns, sort=True, observed=True):
        values = key if isinstance(key, tuple) else (key,)
        rows.append({**dict(zip(columns, values)), **summarize(group)})
    return pd.DataFrame(rows)


def main() -> None:
    trades = pd.read_csv(RESULTS / "phase5_walkforward_trades.csv.gz")
    focus = trades.loc[trades["policy"].isin(["regime_forced", "fixed_k_1.25"])].copy()

    aggregate = grouped(focus, ["policy"])
    aggregate.to_csv(RESULTS / "phase5_policy_exit_cost_summary.csv", index=False)
    grouped(focus, ["policy", "fold"]).to_csv(
        RESULTS / "phase5_policy_fold_exit_cost_summary.csv", index=False
    )
    grouped(focus, ["policy", "outcome"]).to_csv(
        RESULTS / "phase5_policy_outcome_breakdown.csv", index=False
    )
    grouped(focus, ["policy", "fold", "outcome"]).to_csv(
        RESULTS / "phase5_fold_outcome_breakdown.csv", index=False
    )
    grouped(focus, ["policy", "symbol", "side", "outcome"]).to_csv(
        RESULTS / "phase5_symbol_side_outcome_breakdown.csv", index=False
    )

    final_cells = pd.read_csv(RESULTS / "phase5_final_training_response.csv")
    final_cells.sort_values(
        ["mean_net_return_bp", "simultaneous_lower_95_bp", "observations"],
        ascending=[False, False, False],
    ).to_csv(RESULTS / "phase5_final_cells_ranked.csv", index=False)

    p4 = json.loads(PHASE4_1.read_text(encoding="utf-8"))
    p5_lookup = {row["policy"]: row for row in aggregate.to_dict(orient="records")}
    comparison_rows: list[dict] = []
    for label, p4_key, p5_key in (
        ("Forced regime map", "forced_regime_policy", "regime_forced"),
        ("Fixed k=1.25", "fixed_k_1_25", "fixed_k_1.25"),
    ):
        old = p4[p4_key]
        new = p5_lookup[p5_key]
        comparison_rows.append(
            {
                "policy": label,
                "phase4_reward_risk": 1.0,
                "phase5_reward_risk": 1.5,
                "phase4_trades": old["trades"],
                "phase5_trades": new["trades"],
                "phase4_tp_rate": old["tp_first_rate"],
                "phase5_tp_rate": new["tp_first_rate"],
                "phase4_sl_rate": old["sl_first_rate"],
                "phase5_sl_rate": new["sl_first_rate"],
                "phase4_timeout_rate": old["timeout_rate"],
                "phase5_timeout_rate": new["timeout_rate"],
                "phase4_signal_path_gross_bp": old["mean_signal_path_gross_bp"],
                "phase5_signal_path_gross_bp": new["mean_signal_path_gross_bp"],
                "phase4_half_cost_bp": old["mean_half_execution_cost_net_bp"],
                "phase5_half_cost_bp": new["mean_half_execution_cost_net_bp"],
                "phase4_full_cost_bp": old["mean_full_cost_net_bp"],
                "phase5_full_cost_bp": new["mean_full_cost_net_bp"],
                "phase4_full_cost_pf": old["full_cost_profit_factor"],
                "phase5_full_cost_pf": new["full_cost_profit_factor"],
            }
        )
    comparison = pd.DataFrame(comparison_rows)
    comparison.to_csv(RESULTS / "phase5_phase4_comparison.csv", index=False)

    audit = focus.loc[focus["fold"].eq("2024 H1")].sort_values(
        ["policy", "symbol", "entry_timestamp_ms", "candidate_id"]
    ).copy()
    for position, (name, source) in enumerate(
        [
            ("signal_time_utc", "signal_timestamp_ms"),
            ("entry_time_utc", "entry_timestamp_ms"),
            ("exit_time_utc", "exit_timestamp_ms"),
            ("extreme_time_utc", "extreme_timestamp_ms"),
        ],
        start=2,
    ):
        audit.insert(
            position,
            name,
            pd.to_datetime(audit[source], unit="ms", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        )
    audit.to_csv(RESULTS / "phase5_2024H1_trade_level_audit.csv", index=False)

    forced = p5_lookup["regime_forced"]
    fixed = p5_lookup["fixed_k_1.25"]
    best = final_cells.sort_values("mean_net_return_bp", ascending=False).iloc[0]
    payload = {
        "diagnostic_only": True,
        "trade_selection_changed": False,
        "phase5_walkforward_trade_rows": int(len(trades)),
        "focus_trade_rows": int(len(focus)),
        "locked_2025_2026_rows_read": 0,
        "forced_regime": forced,
        "fixed_k_1_25": fixed,
        "central_hypothesis": {
            "regime_forced_minus_fixed_full_cost_bp": float(
                forced["mean_full_cost_net_bp"] - fixed["mean_full_cost_net_bp"]
            ),
            "regime_forced_minus_fixed_signal_path_bp": float(
                forced["mean_signal_path_gross_bp"] - fixed["mean_signal_path_gross_bp"]
            ),
            "supported": False,
        },
        "final_training": {
            "cells": int(len(final_cells)),
            "nominal_positive_mean_cells": int(final_cells["mean_net_return_bp"].gt(0).sum()),
            "profit_factor_above_one_cells": int(final_cells["profit_factor"].gt(1).sum()),
            "positive_simultaneous_lower_bound_cells": int(
                final_cells["simultaneous_lower_95_bp"].gt(0).sum()
            ),
            "evidence_gate_passes": int(final_cells["evidence_gate_pass"].sum()),
            "best_cell": {
                "regime": str(best["regime"]),
                "detector_multiplier": float(best["detector_multiplier"]),
                "observations": int(best["observations"]),
                "mean_net_return_bp": float(best["mean_net_return_bp"]),
                "profit_factor": float(best["profit_factor"]),
                "simultaneous_lower_95_bp": float(best["simultaneous_lower_95_bp"]),
                "positive_halfyear_fraction": float(best["positive_halfyear_fraction"]),
                "evidence_gate_pass": bool(best["evidence_gate_pass"]),
            },
        },
        "phase4_phase5_comparison": comparison.to_dict(orient="records"),
        "decision": "NO_TRADE_TERMINATE_RESEARCH_LINE",
        "interpretation": "RR 1.5 did not rescue the frozen taker-executed price-only specification. Maker execution, selectivity, and non-price information remain untested.",
    }
    (RESULTS / "PHASE5_DIAGNOSTIC_SUMMARY.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
