#!/usr/bin/env python3
"""Frozen path diagnostic for a close, equal-price-distance TP/SL bracket."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from phase5_final_rr15 import funding_component_bp, profit_factor, tick_round
from phase5_minimum_tp_diagnostic import (
    POLICIES,
    market_arrays,
    nearest_target,
    verify_sources,
)


PROFIT_FLOOR_BP = 1.5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase5-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "phase3" / "materialized" / "Find Algo",
    )
    return parser.parse_args()


def evaluate(row: pd.Series, market: dict[str, Any], target: float, stop: float,
             fee_rate: float, exit_slip_bp: float) -> dict[str, Any]:
    side = int(row["side"])
    entry_index = int(row["entry_index"])
    entry = float(row["entry_price"])
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
        outcome, exit_minute, reference = "sl_first", sl_minute, stop
    elif tp_minute:
        outcome, exit_minute, reference = "tp_first", tp_minute, target
    else:
        outcome, exit_minute = "timeout", maximum
        reference = float(market["closes"][end - 1])
    tick = float(market["tick"])
    exit_fill = tick_round(reference * (1.0 - side * exit_slip_bp / 10_000.0), tick, -side)
    gross = side * (exit_fill / entry - 1.0) * 10_000.0
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
        "symmetric_outcome": outcome,
        "symmetric_exit_minutes": exit_minute,
        "symmetric_exit_timestamp_ms": exit_ms,
        "symmetric_exit_reference_price": reference,
        "symmetric_exit_fill_price": exit_fill,
        "symmetric_ambiguous_same_minute": int(ambiguous),
        "symmetric_net_before_funding_bp": gross - fee,
        "symmetric_funding_bp": funding,
        "symmetric_net_return_bp": gross - fee + funding,
    }


def summarize(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for policy, subset in frame.groupby("policy", sort=False):
        values = subset["symmetric_net_return_bp"].to_numpy(float)
        positive = values[values > 0]
        negative = -values[values < 0]
        mean_win = float(positive.mean()) if positive.size else math.nan
        mean_loss = float(negative.mean()) if negative.size else math.nan
        rows.append({
            "policy": policy,
            "trades": len(subset),
            "tp_first": int((subset["symmetric_outcome"] == "tp_first").sum()),
            "sl_first": int((subset["symmetric_outcome"] == "sl_first").sum()),
            "timeout": int((subset["symmetric_outcome"] == "timeout").sum()),
            "tp_first_rate": float((subset["symmetric_outcome"] == "tp_first").mean()),
            "sl_first_rate": float((subset["symmetric_outcome"] == "sl_first").mean()),
            "timeout_rate": float((subset["symmetric_outcome"] == "timeout").mean()),
            "same_minute_ambiguous_rate": float(subset["symmetric_ambiguous_same_minute"].mean()),
            "median_exit_minutes": float(subset["symmetric_exit_minutes"].median()),
            "median_target_distance_bp": float(subset["symmetric_target_distance_bp"].median()),
            "median_stop_distance_bp": float(subset["symmetric_stop_distance_bp"].median()),
            "mean_tp_net_bp": float(subset.loc[subset["symmetric_outcome"] == "tp_first", "symmetric_net_return_bp"].mean()),
            "mean_sl_net_bp": float(subset.loc[subset["symmetric_outcome"] == "sl_first", "symmetric_net_return_bp"].mean()),
            "mean_net_return_bp": float(values.mean()),
            "profit_factor": profit_factor(values),
            "mean_positive_trade_bp": mean_win,
            "mean_negative_trade_loss_bp": mean_loss,
            "required_positive_trade_rate_at_observed_payoffs": mean_loss / (mean_win + mean_loss),
        })
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    phase5_dir = args.phase5_dir.resolve()
    data_dir = args.data_dir.resolve()
    protocol = json.loads((phase5_dir / "PHASE5_SYMMETRIC_CLOSE_BRACKET_PROTOCOL.json").read_text())
    if not protocol.get("frozen_before_diagnostic_outcomes"):
        raise RuntimeError("Protocol is not frozen")
    source_check = verify_sources(phase5_dir, data_dir)
    config = json.loads((phase5_dir / "phase5_config.json").read_text())
    fee_rate = float(config["execution"]["taker_fee_bp_per_fill"])
    exit_slip_bp = float(config["execution"]["exit_slippage_bp"])

    trades = pd.read_csv(phase5_dir / "results" / "phase5_walkforward_trades.csv.gz")
    trades = trades[trades["policy"].isin(POLICIES)].copy()
    if int(trades["entry_timestamp_ms"].max()) >= int(pd.Timestamp("2025-01-01T00:00:00Z").timestamp() * 1000):
        raise RuntimeError("Locked 2025+ row encountered")
    markets = {symbol: market_arrays(data_dir, symbol) for symbol in sorted(trades["symbol"].unique())}

    rows = []
    for record in trades.itertuples(index=False):
        item = record._asdict()
        market = markets[str(record.symbol)]
        side = int(record.side)
        entry = float(record.entry_price)
        tick = float(market["tick"])
        target, target_fill, target_net = nearest_target(
            entry, side, tick, exit_slip_bp, fee_rate, PROFIT_FLOOR_BP
        )
        delta = abs(target - entry)
        stop = tick_round(entry - side * delta, tick, -side)
        target_distance = side * (target / entry - 1.0) * 10_000.0
        stop_distance = -side * (stop / entry - 1.0) * 10_000.0
        item.update({
            "symmetric_target_price": target,
            "symmetric_stop_price": stop,
            "symmetric_target_exit_fill_price": target_fill,
            "symmetric_target_net_before_funding_if_hit_bp": target_net,
            "symmetric_target_distance_bp": target_distance,
            "symmetric_stop_distance_bp": stop_distance,
        })
        item.update(evaluate(pd.Series(item), market, target, stop, fee_rate, exit_slip_bp))
        rows.append(item)

    diagnostic = pd.DataFrame(rows)
    if float(diagnostic["symmetric_target_net_before_funding_if_hit_bp"].min()) + 1e-9 < PROFIT_FLOOR_BP:
        raise RuntimeError("Target floor construction failed")
    if not (abs(diagnostic["symmetric_stop_distance_bp"] - diagnostic["symmetric_target_distance_bp"]) <=
            diagnostic["symbol"].map({symbol: market["tick"] for symbol, market in markets.items()}) /
            diagnostic["entry_price"] * 10_000.0 + 1e-9).all():
        raise RuntimeError("Bracket is not symmetric within one tick")

    summary = summarize(diagnostic)
    outcomes = (
        diagnostic.groupby(["policy", "symmetric_outcome"], sort=False)
        .agg(
            trades=("candidate_id", "size"),
            median_exit_minutes=("symmetric_exit_minutes", "median"),
            mean_net_before_funding_bp=("symmetric_net_before_funding_bp", "mean"),
            mean_funding_bp=("symmetric_funding_bp", "mean"),
            mean_net_return_bp=("symmetric_net_return_bp", "mean"),
        )
        .reset_index()
    )
    outcomes["share"] = outcomes["trades"] / outcomes.groupby("policy")["trades"].transform("sum")
    results_dir = phase5_dir / "results"
    summary.to_csv(results_dir / "phase5_symmetric_close_bracket_summary.csv", index=False)
    outcomes.to_csv(results_dir / "phase5_symmetric_close_bracket_outcomes.csv", index=False)
    diagnostic.to_csv(results_dir / "phase5_symmetric_close_bracket_trade_audit.csv.gz", index=False, compression="gzip")
    payload = {
        "protocol": protocol,
        "source_validation": {
            "files_checked": source_check["files_checked"],
            "all_hashes_match": source_check["all_hashes_match"],
            "candle_years_read": [2021, 2022, 2023, 2024],
            "locked_2025_2026_rows_read": 0,
        },
        "summary": summary.to_dict("records"),
        "outcomes": outcomes.to_dict("records"),
        "cost_identity": {
            "literal_plus_minus_1_5_bp_price_bracket": "Both exits remain net-negative because round-trip taker fees and slippage exceed 1.5 bp.",
            "equal_price_distance_warning": "A target netting +1.5 bp and an equally distant adverse stop do not generate +/-1.5 bp net outcomes.",
        },
        "limitations": protocol["limitations"],
    }
    (results_dir / "PHASE5_SYMMETRIC_CLOSE_BRACKET_DIAGNOSTIC.json").write_text(
        json.dumps(payload, indent=2) + "\n"
    )
    print(summary.to_string(index=False))
    print("\n", outcomes.to_string(index=False))


if __name__ == "__main__":
    main()
