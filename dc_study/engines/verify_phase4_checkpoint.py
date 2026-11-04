#!/usr/bin/env python3
"""Verify the persisted Phase 4 checkpoint without rerunning the research."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "PHASE4_INTEGRITY_MANIFEST.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for item in manifest["artifacts"]:
        path = ROOT / item["path"]
        require(path.is_file(), f"missing artifact: {item['path']}")
        require(path.stat().st_size == int(item["size_bytes"]), f"size mismatch: {item['path']}")
        require(sha256(path) == item["sha256"], f"hash mismatch: {item['path']}")

    source_audit = pd.read_csv(ROOT / "results/phase4_source_hash_audit.csv")
    candle_audit = pd.read_csv(ROOT / "results/phase4_candle_integrity_audit.csv")
    require(len(source_audit) == 12 and bool(source_audit["passed"].all()), "source audit failed")
    require(len(candle_audit) == 8 and bool(candle_audit["passed"].all()), "candle audit failed")
    require(int(candle_audit["rows"].sum()) == 3_911_906, "candle row total mismatch")

    compressed = ROOT / "phase4_candidates_2021_2024.sqlite.gz"
    with tempfile.TemporaryDirectory(prefix="phase4-verify-") as directory:
        database = Path(directory) / "candidates.sqlite"
        with gzip.open(compressed, "rb") as source, database.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        connection = sqlite3.connect(database)
        try:
            require(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity failed")
            count = int(connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0])
            require(count == int(manifest["integrity"]["candidate_rows"]), "candidate count mismatch")
            locked = int(
                connection.execute(
                    "SELECT COUNT(*) FROM candidates WHERE entry_timestamp_ms >= ?",
                    (1735689600000,),
                ).fetchone()[0]
            )
            require(locked == 0, "2025+ candidate found")
            unique = int(connection.execute("SELECT COUNT(DISTINCT candidate_id) FROM candidates").fetchone()[0])
            require(unique == count, "candidate_id is not unique")
            theta_error = float(
                connection.execute(
                    "SELECT MAX(ABS(theta_bp - detector_multiplier * sigma_bp)) FROM candidates"
                ).fetchone()[0]
            )
            require(theta_error <= 1e-9, f"dynamic threshold arithmetic error: {theta_error}")
            geometry = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM candidates
                    WHERE (side=1 AND NOT(stop_price < entry_price AND target_price > entry_price))
                       OR (side=-1 AND NOT(target_price < entry_price AND stop_price > entry_price))
                    """
                ).fetchone()[0]
            )
            require(geometry == 0, "invalid stop/entry/target ordering")
            tick_geometry = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM candidates
                    WHERE ABS(
                        CASE WHEN side=1
                             THEN (target_price-entry_price)-(entry_price-stop_price)
                             ELSE (entry_price-target_price)-(stop_price-entry_price)
                        END
                    ) > CASE WHEN symbol='ETHUSDT' THEN 0.010000001 ELSE 0.000100001 END
                    """
                ).fetchone()[0]
            )
            require(tick_geometry == 0, "1:1 target geometry exceeds one tick")
            accounting_error = float(
                connection.execute(
                    "SELECT MAX(ABS(net_return_bp-(gross_after_slippage_bp-fee_bp+funding_bp))) FROM candidates"
                ).fetchone()[0]
            )
            require(accounting_error <= 1e-9, f"cost accounting mismatch: {accounting_error}")
        finally:
            connection.close()

    response = pd.read_csv(ROOT / "results/phase4_training_response_by_fold.csv")
    require(len(response) == 216, "training response must have 216 cells")
    require(int(response["evidence_gate_pass"].sum()) == 0, "unexpected evidence pass")
    require(set(response["fold"]) == {"2023 H1", "2023 H2", "2024 H1", "2024 H2"}, "fold mismatch")

    trades = pd.read_csv(ROOT / "results/phase4_walkforward_trades.csv.gz")
    require(len(trades) == int(manifest["integrity"]["walk_forward_rows"]), "walk-forward row mismatch")
    require(not (trades["policy"] == "regime_evidence").any(), "evidence policy traded")
    require(int((trades["policy"] == "regime_forced").sum()) == 3923, "forced-policy count mismatch")
    maximum_trade_error = float(
        (trades["net_return_bp"] - (trades["gross_after_slippage_bp"] - trades["fee_bp"] + trades["funding_bp"])).abs().max()
    )
    require(maximum_trade_error <= 1e-9, "trade-table accounting mismatch")

    aggregate = pd.read_csv(ROOT / "results/phase4_walkforward_aggregate.csv")
    evidence = aggregate.loc[aggregate["policy"] == "regime_evidence"].iloc[0]
    forced = aggregate.loc[aggregate["policy"] == "regime_forced"].iloc[0]
    require(int(evidence["trades"]) == 0, "evidence aggregate mismatch")
    require(int(forced["trades"]) == 3923, "forced aggregate mismatch")
    require(float(forced["mean_net_return_bp"]) < 0, "forced policy unexpectedly positive")

    results = json.loads((ROOT / "results/PHASE4_REGIME_NORMALIZED_RESULTS.json").read_text(encoding="utf-8"))
    require(results["result"]["decision"] == "NO_TRADE", "decision mismatch")
    require(results["data_integrity"]["locked_2025_2026_price_rows_read"] == 0, "locked data opened")
    require(results["locked_test_access"]["opened"] is False, "locked-test flag mismatch")

    reproducibility = manifest["reproducibility"]
    require(reproducibility["principal_outputs_byte_identical"] is True, "reproducibility flag failed")
    print("Phase 4 checkpoint verification: PASS")
    print(f"candidates={count:,}; walk_forward_rows={len(trades):,}; evidence_passes=0")
    print("locked_2025_2026_price_rows=0; database_integrity=ok; hashes=ok")


if __name__ == "__main__":
    main()
