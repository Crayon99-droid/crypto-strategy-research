#!/usr/bin/env python3
"""Verify the final Phase 5 checkpoint and optionally write its hash manifest."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
RESULTS = ROOT / "results"
REPRO_RESULTS = ROOT / "repro_check" / "results"
WORKBOOK = PROJECT / "outputs" / "phase5_final" / "Phase5_Final_RR15_Audit.xlsx"
MANIFEST = ROOT / "PHASE5_INTEGRITY_MANIFEST.json"

ROOT_ARTIFACTS = [
    ROOT / "PHASE5_FROZEN_PROTOCOL.json",
    ROOT / "phase5_config.json",
    ROOT / "phase5_final_rr15.py",
    ROOT / "phase5_diagnostics.py",
    ROOT / "PHASE5_FINAL_REPORT.md",
    ROOT / "phase5_candidates_2021_2024.sqlite.gz",
    ROOT / "verify_phase5_checkpoint.py",
    WORKBOOK,
]
RESULT_ARTIFACTS = sorted(path for path in RESULTS.iterdir() if path.is_file())
ARTIFACTS = ROOT_ARTIFACTS + RESULT_ARTIFACTS

DETERMINISTIC_FILES = [
    "phase5_source_hash_audit.csv",
    "phase5_candle_integrity_audit.csv",
    "phase5_training_response_by_fold.csv",
    "phase5_walkforward_threshold_maps.csv",
    "phase5_walkforward_trades.csv.gz",
    "phase5_walkforward_fold_summary.csv",
    "phase5_walkforward_aggregate.csv",
    "phase5_walkforward_side_diagnostic.csv",
    "phase5_walkforward_regime_diagnostic.csv",
    "phase5_walkforward_regime_side_diagnostic.csv",
    "phase5_walkforward_symbol_side_diagnostic.csv",
    "phase5_final_training_response.csv",
    "phase5_final_2025_threshold_map.csv",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def csv_rows(path: Path) -> int:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        return sum(1 for _ in reader)


def verify_config() -> None:
    phase4 = load_json(PROJECT / "phase4" / "phase4_config.json")
    phase5 = load_json(ROOT / "phase5_config.json")
    for value in (phase4, phase5):
        value.pop("research_name", None)
        value.pop("notes", None)
    rr4 = phase4["trade"].pop("reward_risk")
    rr5 = phase5["trade"].pop("reward_risk")
    assert rr4 == 1.0 and rr5 == 1.5
    assert phase4 == phase5, "A frozen parameter other than reward/risk changed"
    protocol = load_json(ROOT / "PHASE5_FROZEN_PROTOCOL.json")
    assert protocol["frozen_before_phase5_outcomes"] is True
    assert protocol["only_changed_parameter"]["phase5_value"] == 1.5
    assert protocol["parameter_changes_after_outcome"] is False


def decompress_database(archive: Path, destination: Path) -> None:
    with gzip.open(archive, "rb") as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)


def verify_database_and_lineage() -> dict:
    with tempfile.TemporaryDirectory(prefix="phase5_verify_") as temp:
        temp_path = Path(temp)
        phase5_db = temp_path / "phase5.sqlite"
        phase4_db = temp_path / "phase4.sqlite"
        decompress_database(ROOT / "phase5_candidates_2021_2024.sqlite.gz", phase5_db)
        decompress_database(PROJECT / "phase4" / "phase4_candidates_2021_2024.sqlite.gz", phase4_db)
        connection = sqlite3.connect(phase5_db)
        try:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            connection.execute("ATTACH DATABASE ? AS p4", (str(phase4_db),))
            assert connection.execute("PRAGMA p4.integrity_check").fetchone()[0] == "ok"
            count5 = int(connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0])
            count4 = int(connection.execute("SELECT COUNT(*) FROM p4.candidates").fetchone()[0])
            matched = int(
                connection.execute(
                    "SELECT COUNT(*) FROM candidates JOIN p4.candidates USING(candidate_id)"
                ).fetchone()[0]
            )
            columns = [
                "symbol", "detector_multiplier", "detector_sequence", "side",
                "signal_index", "signal_timestamp_ms", "signal_close", "extreme_index",
                "extreme_timestamp_ms", "extreme_price", "sigma_bp", "theta_bp",
                "rv240_bp", "return240_bp", "trend_score_abs", "vol_level",
                "structure", "regime", "entry_index", "entry_timestamp_ms",
                "entry_open", "entry_price", "stop_price", "risk_bp", "week_cluster",
                "halfyear",
            ]
            where = " OR ".join(f"NOT (c.{column} IS q.{column})" for column in columns)
            mismatches = int(
                connection.execute(
                    "SELECT COUNT(*) FROM candidates c JOIN p4.candidates q USING(candidate_id) WHERE " + where
                ).fetchone()[0]
            )
            assert count5 == count4 == matched == 388_338
            assert mismatches == 0
            geometry_bad = {}
            for symbol, tick in (("ETHUSDT", 0.01), ("XRPUSDT", 0.0001)):
                bad = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM candidates WHERE symbol=? "
                        "AND ABS(ABS(target_price-entry_price)-1.5*ABS(entry_price-stop_price))>?",
                        (symbol, tick + 1e-9),
                    ).fetchone()[0]
                )
                assert bad == 0
                geometry_bad[symbol] = bad
        finally:
            connection.close()
    return {
        "candidate_rows": count5,
        "matched_phase4_candidate_ids": matched,
        "stable_lineage_mismatches": mismatches,
        "rr15_geometry_outside_one_tick": geometry_bad,
        "sqlite_integrity": "ok",
    }


def verify_semantics() -> dict:
    verify_config()
    result = load_json(RESULTS / "PHASE5_REGIME_NORMALIZED_RESULTS.json")
    diagnostic = load_json(RESULTS / "PHASE5_DIAGNOSTIC_SUMMARY.json")
    assert result["data_integrity"]["source_files_passed"] == 12
    assert result["data_integrity"]["candle_rows_checked"] == 3_911_906
    assert result["data_integrity"]["candidate_rows"] == 388_338
    assert result["data_integrity"]["locked_2025_2026_price_rows_read"] == 0
    assert result["result"]["final_training_cells"] == 54
    assert result["result"]["final_nominal_positive_training_cells"] == 0
    assert result["result"]["final_familywise_evidence_passes"] == 0
    assert result["result"]["final_evidence_map"] == {}
    assert result["result"]["decision"] == "NO_TRADE_TERMINATE_RESEARCH_LINE"
    assert result["central_hypothesis"]["supported"] is False
    assert result["central_hypothesis"]["regime_minus_fixed_mean_net_bp"] < -11.0
    assert diagnostic["phase5_walkforward_trade_rows"] == 96_211
    assert diagnostic["locked_2025_2026_rows_read"] == 0

    expected_rows = {
        "phase5_training_response_by_fold.csv": 216,
        "phase5_final_training_response.csv": 54,
        "phase5_final_2025_threshold_map.csv": 6,
        "phase5_walkforward_aggregate.csv": 11,
        "phase5_walkforward_fold_summary.csv": 44,
        "phase5_policy_exit_cost_summary.csv": 2,
        "phase5_policy_fold_exit_cost_summary.csv": 8,
        "phase5_policy_outcome_breakdown.csv": 6,
        "phase5_2024H1_trade_level_audit.csv": 1_787,
        "phase5_walkforward_trades.csv.gz": 96_211,
    }
    for name, expected in expected_rows.items():
        observed = csv_rows(RESULTS / name)
        assert observed == expected, (name, observed, expected)

    with (RESULTS / "phase5_final_2025_threshold_map.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        final_map = list(csv.DictReader(handle))
    assert len(final_map) == 6
    assert all(row["no_trade"] == "True" and row["detector_multiplier"] == "" for row in final_map)

    for name in DETERMINISTIC_FILES:
        assert sha256(RESULTS / name) == sha256(REPRO_RESULTS / name), name
    for name in ("phase5_collection_summary.json", "PHASE5_REGIME_NORMALIZED_RESULTS.json"):
        original = load_json(RESULTS / name)
        reproduced = load_json(REPRO_RESULTS / name)
        for value in (original, reproduced):
            value.pop("created_utc", None)
            value.pop("elapsed_seconds", None)
        assert original == reproduced, name

    assert WORKBOOK.is_file() and WORKBOOK.stat().st_size > 100_000
    formula_scan = (ROOT / "phase5_workbook_formula_errors.ndjson").read_text(encoding="utf-8")
    assert "matched 0 entries" in formula_scan and "#REF!" not in formula_scan
    report = (ROOT / "PHASE5_FINAL_REPORT.md").read_text(encoding="utf-8")
    assert "The central hypothesis failed" in report
    assert "TERMINATE THIS RESEARCH LINE" in report
    assert "Price rows read from 2025–2026: **0**" in report

    lineage = verify_database_and_lineage()
    return {
        "source_files_passed": 12,
        "candle_rows_checked": 3_911_906,
        "candidate_rows": 388_338,
        "walkforward_trade_rows": 96_211,
        "final_cells": 54,
        "final_evidence_passes": 0,
        "locked_2025_2026_rows_read": 0,
        "central_hypothesis_supported": False,
        "decision": "NO_TRADE_TERMINATE_RESEARCH_LINE",
        "independent_reproduction": "passed",
        "candidate_lineage": lineage,
        "csv_row_counts": expected_rows,
    }


def build_manifest(semantics: dict) -> dict:
    for path in ARTIFACTS:
        assert path.is_file(), path
    source_inputs = [
        PROJECT / "phase4_1" / "PHASE4_1_PREREGISTRATION.json",
        PROJECT / "phase4" / "PHASE4_INTEGRITY_MANIFEST.json",
        PROJECT / "phase4" / "phase4_candidates_2021_2024.sqlite.gz",
        PROJECT / "phase3" / "materialized" / "bybit_data_validation_report.json",
    ]
    return {
        "checkpoint": "Phase 5 Final RR 1.5 Directional-Change Test",
        "status": "final_pre_2025_research_line_terminated",
        "semantic_checks": semantics,
        "artifact_hashes_sha256": {
            str(path.relative_to(PROJECT)): sha256(path) for path in ARTIFACTS
        },
        "frozen_source_hashes_sha256": {
            str(path.relative_to(PROJECT)): sha256(path) for path in source_inputs
        },
        "deterministic_reproduction_hashes_sha256": {
            name: sha256(RESULTS / name) for name in DETERMINISTIC_FILES
        },
        "manifest_self_hash_excluded": True,
    }


def verify_manifest(manifest: dict) -> None:
    for group in ("artifact_hashes_sha256", "frozen_source_hashes_sha256"):
        for relative, expected in manifest[group].items():
            path = PROJECT / relative
            assert path.is_file(), path
            assert sha256(path) == expected, relative


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    semantics = verify_semantics()
    if args.write_manifest:
        manifest = build_manifest(semantics)
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    else:
        assert MANIFEST.is_file(), MANIFEST
        manifest = load_json(MANIFEST)
        verify_manifest(manifest)
        assert manifest["semantic_checks"] == semantics
    print("Phase 5 checkpoint: PASS")
    print(json.dumps(semantics, indent=2))


if __name__ == "__main__":
    main()
