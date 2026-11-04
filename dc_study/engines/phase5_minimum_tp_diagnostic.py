#!/usr/bin/env python3
"""Frozen diagnostic of Phase 5 stop/target geometry and minimum profitable TP.

This script intentionally reuses the already-selected Phase 5 trades. It does
not reselect signals after an earlier diagnostic exit and is therefore a path
diagnostic, not a new causal strategy backtest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from phase5_final_rr15 import (
    funding_component_bp,
    load_market,
    profit_factor,
    tick_round,
)


POLICIES = ("fixed_k_1.25", "regime_forced")
PROFIT_FLOOR_BP = 1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase5-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "phase3" / "materialized" / "Find Algo",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sources(phase5_dir: Path, data_dir: Path) -> dict[str, Any]:
    audit = pd.read_csv(phase5_dir / "results" / "phase5_source_hash_audit.csv")
    if not bool(audit["passed"].all()):
        raise RuntimeError("Frozen Phase 5 source audit contains a failed row")
    rows = []
    for record in audit.to_dict("records"):
        path = data_dir / str(record["file"])
        actual = sha256(path)
        expected = str(record["expected_sha256"])
        rows.append({"file": path.name, "sha256": actual, "matches_frozen_audit": actual == expected})
    if not all(row["matches_frozen_audit"] for row in rows):
        raise RuntimeError("Current source bytes do not match the frozen Phase 5 audit")
    return {"files_checked": len(rows), "all_hashes_match": True, "rows": rows}


def market_arrays(data_dir: Path, symbol: str) -> dict[str, Any]:
    bars, funding, instrument = load_market(data_dir, symbol)
    timestamps = bars["timestamp_ms"].to_numpy(np.int64)
    if int(timestamps.max()) >= int(pd.Timestamp("2025-01-01T00:00:00Z").timestamp() * 1000):
        raise RuntimeError(f"{symbol} includes a locked 2025+ candle")
    rates = funding["funding_rate"].to_numpy(float)
    return {
        "timestamps": timestamps,
        "highs": bars["high"].to_numpy(float),
        "lows": bars["low"].to_numpy(float),
        "closes": bars["close"].to_numpy(float),
        "tick": float(instrument["priceFilter"]["tickSize"]),
        "funding_times": funding["timestamp_ms"].to_numpy(np.int64),
        "funding_prefix": np.concatenate(([0.0], np.cumsum(rates))),
    }


def exit_net_before_funding_bp(entry: float, exit_fill: float, side: int, fee_bp: float) -> float:
    gross = side * (exit_fill / entry - 1.0) * 10_000.0
    fees = fee_bp + fee_bp * exit_fill / entry
    return gross - fees


def nearest_target(
    entry: float,
    side: int,
    tick: float,
    exit_slip_bp: float,
    fee_bp: float,
    desired_net_bp: float,
) -> tuple[float, float, float]:
    """Return target reference, modeled exit fill, and net before funding."""
    slip = exit_slip_bp / 10_000.0
    if side == 1:
        required_ratio = (10_000.0 + fee_bp + desired_net_bp) / (10_000.0 - fee_bp)
        required_fill = tick_round(entry * required_ratio, tick, 1)
        target = tick_round(required_fill / (1.0 - slip), tick, 1)
    else:
        maximum_ratio = (10_000.0 - fee_bp - desired_net_bp) / (10_000.0 + fee_bp)
        maximum_fill = tick_round(entry * maximum_ratio, tick, -1)
        target = tick_round(maximum_fill / (1.0 + slip), tick, -1)

    # Tick arithmetic near a boundary can miss by one tick. Move only as far as
    # required to satisfy the frozen net-return floor.
    for _ in range(8):
        exit_raw = target * (1.0 - side * slip)
        exit_fill = tick_round(exit_raw, tick, -side)
        net = exit_net_before_funding_bp(entry, exit_fill, side, fee_bp)
        if net + 1e-10 >= desired_net_bp:
            closer = target - side * tick
            closer_fill = tick_round(closer * (1.0 - side * slip), tick, -side)
            closer_net = exit_net_before_funding_bp(entry, closer_fill, side, fee_bp)
            if closer_net + 1e-10 >= desired_net_bp:
                target = closer
                continue
            return target, exit_fill, net
        target += side * tick
    raise RuntimeError("Failed to construct the nearest tick-valid target")


def evaluate_minimum_tp(
    row: pd.Series,
    market: dict[str, Any],
    target: float,
    fee_rate: float,
    exit_slip_bp: float,
) -> dict[str, Any]:
    side = int(row["side"])
    entry_index = int(row["entry_index"])
    entry = float(row["entry_price"])
    stop = float(row["stop_price"])
    maximum = 240
    end = entry_index + maximum
    highs = market["highs"][entry_index:end]
    lows = market["lows"][entry_index:end]
    if len(highs) != maximum:
        raise RuntimeError("Incomplete diagnostic holding window")
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

    tick = float(market["tick"])
    slip = exit_slip_bp / 10_000.0
    exit_fill = tick_round(exit_reference * (1.0 - side * slip), tick, -side)
    gross_after_slippage = side * (exit_fill / entry - 1.0) * 10_000.0
    fee = fee_rate + fee_rate * exit_fill / entry
    exit_ms = int(market["timestamps"][entry_index + exit_minute - 1])
    funding = funding_component_bp(
        side,
        int(market["timestamps"][entry_index]),
        exit_ms,
        market["funding_times"],
        market["funding_prefix"],
    )
    return {
        "diagnostic_outcome": outcome,
        "diagnostic_exit_minutes": exit_minute,
        "diagnostic_exit_timestamp_ms": exit_ms,
        "diagnostic_exit_reference_price": exit_reference,
        "diagnostic_exit_fill_price": exit_fill,
        "diagnostic_ambiguous_same_minute": int(ambiguous),
        "diagnostic_fee_bp": fee,
        "diagnostic_funding_bp": funding,
        "diagnostic_net_before_funding_bp": gross_after_slippage - fee,
        "diagnostic_net_return_bp": gross_after_slippage - fee + funding,
    }


def quantiles(frame: pd.DataFrame, columns: list[str], group: str) -> pd.DataFrame:
    levels = [("min", 0.0), ("p10", 0.10), ("p25", 0.25), ("median", 0.50),
              ("p75", 0.75), ("p90", 0.90), ("max", 1.0)]
    rows: list[dict[str, Any]] = []
    for key, subset in frame.groupby(group, sort=False):
        for column in columns:
            item: dict[str, Any] = {group: key, "metric": column, "count": len(subset)}
            for label, q in levels:
                item[label] = float(subset[column].quantile(q))
            item["mean"] = float(subset[column].mean())
            rows.append(item)
    return pd.DataFrame(rows)


def summarize_policy(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for policy, subset in frame.groupby("policy", sort=False):
        values = subset["diagnostic_net_return_bp"].to_numpy(float)
        positive = values[values > 0]
        negative = -values[values < 0]
        mean_win = float(positive.mean()) if positive.size else math.nan
        mean_loss = float(negative.mean()) if negative.size else math.nan
        rows.append({
            "policy": policy,
            "trades": len(subset),
            "tp_first": int((subset["diagnostic_outcome"] == "tp_first").sum()),
            "sl_first": int((subset["diagnostic_outcome"] == "sl_first").sum()),
            "timeout": int((subset["diagnostic_outcome"] == "timeout").sum()),
            "tp_first_rate": float((subset["diagnostic_outcome"] == "tp_first").mean()),
            "sl_first_rate": float((subset["diagnostic_outcome"] == "sl_first").mean()),
            "timeout_rate": float((subset["diagnostic_outcome"] == "timeout").mean()),
            "median_exit_minutes": float(subset["diagnostic_exit_minutes"].median()),
            "mean_net_return_bp": float(values.mean()),
            "profit_factor": profit_factor(values),
            "mean_positive_trade_bp": mean_win,
            "mean_negative_trade_loss_bp": mean_loss,
            "required_positive_trade_rate_at_observed_payoffs": mean_loss / (mean_win + mean_loss),
            "mean_funding_bp": float(subset["diagnostic_funding_bp"].mean()),
            "median_original_stop_distance_bp": float(subset["original_stop_distance_bp"].median()),
            "median_original_target_distance_bp": float(subset["original_target_distance_bp"].median()),
            "median_min_profit_target_distance_bp": float(subset["min_profit_target_distance_bp"].median()),
            "median_min_profit_distance_from_entry_open_bp": float(subset["min_profit_distance_from_entry_open_bp"].median()),
            "median_min_profit_reward_risk": float(subset["min_profit_reward_risk"].median()),
        })
    return pd.DataFrame(rows)


def outcome_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (policy, outcome), subset in frame.groupby(["policy", "diagnostic_outcome"], sort=False):
        rows.append({
            "policy": policy,
            "outcome": outcome,
            "trades": len(subset),
            "share": len(subset) / int((frame["policy"] == policy).sum()),
            "median_exit_minutes": float(subset["diagnostic_exit_minutes"].median()),
            "mean_net_before_funding_bp": float(subset["diagnostic_net_before_funding_bp"].mean()),
            "mean_funding_bp": float(subset["diagnostic_funding_bp"].mean()),
            "mean_net_return_bp": float(subset["diagnostic_net_return_bp"].mean()),
        })
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    phase5_dir = args.phase5_dir.resolve()
    data_dir = args.data_dir.resolve()
    protocol = json.loads((phase5_dir / "PHASE5_MINIMUM_TP_DIAGNOSTIC_PROTOCOL.json").read_text())
    if not protocol.get("frozen_before_diagnostic_outcomes"):
        raise RuntimeError("Diagnostic protocol is not frozen")
    source_check = verify_sources(phase5_dir, data_dir)

    trades = pd.read_csv(phase5_dir / "results" / "phase5_walkforward_trades.csv.gz")
    trades = trades[trades["policy"].isin(POLICIES)].copy()
    trades.sort_values(["policy", "symbol", "entry_timestamp_ms"], inplace=True)
    if int(trades["entry_timestamp_ms"].max()) >= int(pd.Timestamp("2025-01-01T00:00:00Z").timestamp() * 1000):
        raise RuntimeError("Locked 2025+ trade row encountered")

    config = json.loads((phase5_dir / "phase5_config.json").read_text())
    fee_rate = float(config["execution"]["taker_fee_bp_per_fill"])
    exit_slip_bp = float(config["execution"]["exit_slippage_bp"])
    markets = {symbol: market_arrays(data_dir, symbol) for symbol in sorted(trades["symbol"].unique())}

    diagnostic_rows: list[dict[str, Any]] = []
    for row in trades.itertuples(index=False):
        item = row._asdict()
        market = markets[str(row.symbol)]
        side = int(row.side)
        entry = float(row.entry_price)
        tick = float(market["tick"])
        break_even_target, break_even_fill, break_even_net = nearest_target(
            entry, side, tick, exit_slip_bp, fee_rate, 0.0
        )
        minimum_target, minimum_fill, minimum_net = nearest_target(
            entry, side, tick, exit_slip_bp, fee_rate, PROFIT_FLOOR_BP
        )
        stop_distance = side * (entry / float(row.stop_price) - 1.0) * -10_000.0
        # The direction-neutral distances below are positive by construction.
        stop_distance = abs(entry - float(row.stop_price)) / entry * 10_000.0
        original_target_distance = side * (float(row.target_price) / entry - 1.0) * 10_000.0
        break_even_distance = side * (break_even_target / entry - 1.0) * 10_000.0
        minimum_distance = side * (minimum_target / entry - 1.0) * 10_000.0
        item.update({
            "original_stop_distance_bp": stop_distance,
            "original_target_distance_bp": original_target_distance,
            "break_even_target_price": break_even_target,
            "break_even_exit_fill_price": break_even_fill,
            "break_even_target_distance_bp": break_even_distance,
            "break_even_net_before_funding_bp": break_even_net,
            "min_profit_target_price": minimum_target,
            "min_profit_exit_fill_price": minimum_fill,
            "min_profit_target_distance_bp": minimum_distance,
            "min_profit_distance_from_entry_open_bp": side * (minimum_target / float(row.entry_open) - 1.0) * 10_000.0,
            "min_profit_net_before_funding_if_hit_bp": minimum_net,
            "min_profit_reward_risk": minimum_distance / stop_distance,
        })
        item.update(evaluate_minimum_tp(pd.Series(item), market, minimum_target, fee_rate, exit_slip_bp))
        diagnostic_rows.append(item)

    diagnostic = pd.DataFrame(diagnostic_rows)
    if float(diagnostic["min_profit_net_before_funding_if_hit_bp"].min()) + 1e-9 < PROFIT_FLOOR_BP:
        raise RuntimeError("A minimum-profit target failed its +1 bp construction")
    if not (diagnostic["min_profit_target_distance_bp"] + 1e-10 >= diagnostic["break_even_target_distance_bp"]).all():
        raise RuntimeError("Minimum-profit target is closer than break-even")

    results_dir = phase5_dir / "results"
    policy = summarize_policy(diagnostic)
    outcomes = outcome_summary(diagnostic)
    transitions = (
        diagnostic.groupby(["policy", "outcome", "diagnostic_outcome"], sort=False)
        .size()
        .rename("trades")
        .reset_index()
    )
    transitions["share_within_original_outcome"] = transitions["trades"] / transitions.groupby(
        ["policy", "outcome"]
    )["trades"].transform("sum")
    distances = quantiles(
        diagnostic,
        [
            "original_stop_distance_bp",
            "original_target_distance_bp",
            "break_even_target_distance_bp",
            "min_profit_target_distance_bp",
            "min_profit_reward_risk",
        ],
        "policy",
    )
    policy.to_csv(results_dir / "phase5_minimum_tp_policy_summary.csv", index=False)
    outcomes.to_csv(results_dir / "phase5_minimum_tp_outcome_summary.csv", index=False)
    transitions.to_csv(results_dir / "phase5_minimum_tp_transition_summary.csv", index=False)
    distances.to_csv(results_dir / "phase5_minimum_tp_distance_quantiles.csv", index=False)
    diagnostic.to_csv(results_dir / "phase5_minimum_tp_trade_audit.csv.gz", index=False, compression="gzip")

    payload = {
        "protocol": protocol,
        "source_validation": {
            "files_checked": source_check["files_checked"],
            "all_hashes_match": source_check["all_hashes_match"],
            "candle_years_read": [2021, 2022, 2023, 2024],
            "locked_2025_2026_rows_read": 0,
        },
        "construction": {
            "fee_bp_per_fill": fee_rate,
            "exit_slippage_bp": exit_slip_bp,
            "profit_floor_bp_excluding_funding": PROFIT_FLOOR_BP,
            "long_exit_fill_ratio_floor_formula": "(10000 + fee + desired_net) / (10000 - fee)",
            "short_exit_fill_ratio_ceiling_formula": "(10000 - fee - desired_net) / (10000 + fee)",
        },
        "policy_summary": policy.to_dict("records"),
        "outcome_summary": outcomes.to_dict("records"),
        "original_to_diagnostic_outcomes": transitions.to_dict("records"),
        "distance_quantiles": distances.to_dict("records"),
        "limitations": [
            "The +1 bp target excludes future-unknown funding when it is set; realized historical funding is included in diagnostic returns.",
            "This reuses the frozen Phase 5 trade set. Earlier exits would change later non-overlap eligibility in a fully causal rerun.",
            "A close target can reduce timeouts while remaining economically inferior because the original stop loss is unchanged.",
        ],
    }
    (results_dir / "PHASE5_MINIMUM_TP_DIAGNOSTIC.json").write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n"
    )
    print(policy.to_string(index=False))
    print("\nDistance quantiles:\n", distances.to_string(index=False))
    print("\nOutcome summary:\n", outcomes.to_string(index=False))


if __name__ == "__main__":
    main()
