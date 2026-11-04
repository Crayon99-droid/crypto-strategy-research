#!/usr/bin/env python3
"""Verify the Phase 4.1 diagnostic amendment and optionally build its manifest."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
RESULTS = ROOT / "results"
MANIFEST = ROOT / "PHASE4_1_INTEGRITY_MANIFEST.json"

ARTIFACTS = [
    ROOT / "PHASE4_1_PREREGISTRATION.json",
    ROOT / "PHASE4_1_DIAGNOSTIC_AMENDMENT.md",
    ROOT / "Phase4_1_Diagnostic_Amendment.xlsx",
    ROOT / "phase4_1_diagnostics.py",
    ROOT / "verify_phase4_1_checkpoint.py",
    RESULTS / "PHASE4_1_DIAGNOSTIC_RESULTS.json",
    RESULTS / "phase4_1_training_cell_exit_breakdown.csv",
    RESULTS / "phase4_1_policy_fold_exit_cost_summary.csv",
    RESULTS / "phase4_1_walkforward_cell_exit_cost_summary.csv",
    RESULTS / "phase4_1_policy_fold_side_exit_cost_summary.csv",
    RESULTS / "phase4_1_detailed_exit_reason_breakdown.csv",
    RESULTS / "phase4_1_time_to_resolution.csv",
    RESULTS / "phase4_2024H1_forced_regime_trade_level.csv",
    RESULTS / "phase4_2024H1_all_policy_trade_level.csv.gz",
]

SOURCE_INPUTS = [
    PROJECT / "phase4" / "phase4_candidates_2021_2024.sqlite.gz",
    PROJECT / "phase4" / "results" / "phase4_walkforward_trades.csv.gz",
    PROJECT / "phase4" / "PHASE4_INTEGRITY_MANIFEST.json",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def csv_rows(path: Path) -> int:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        return sum(1 for _ in reader)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def verify_semantics() -> dict:
    prereg = load_json(ROOT / "PHASE4_1_PREREGISTRATION.json")
    results = load_json(RESULTS / "PHASE4_1_DIAGNOSTIC_RESULTS.json")

    assert prereg["frozen_before_phase4_1_diagnostic_calculation"] is True
    assert prereg["sequential_experiment_cap"]["maximum_additional_reformulations_before_2025"] == 1
    assert prereg["sequential_experiment_cap"]["trend_alignment_reformulation_allowed"] is False
    assert prereg["phase5_if_permitted"]["only_concept_allowed_to_change"] == "reward-risk ratio"
    assert prereg["phase5_if_permitted"]["reward_risk"] == 1.5
    assert prereg["holdout_sequence"]["parameter_changes_after_2025_access"] is False

    assert results["diagnostic_only"] is True
    assert results["trade_selection_changed"] is False
    assert results["phase4_trade_rows_read"] == 118_138
    assert results["training_cells_decomposed"] == 216
    assert results["locked_2025_2026_price_rows_read"] == 0
    assert results["phase5_permission_gate"]["phase5_permitted"] is True
    assert results["fixed_k_1_25"]["positive_signal_path_folds"] == 3

    expected_rows = {
        "phase4_1_training_cell_exit_breakdown.csv": 216,
        "phase4_1_policy_fold_exit_cost_summary.csv": 40,
        "phase4_1_walkforward_cell_exit_cost_summary.csv": 240,
        "phase4_1_policy_fold_side_exit_cost_summary.csv": 80,
        "phase4_1_detailed_exit_reason_breakdown.csv": 2702,
        "phase4_1_time_to_resolution.csv": 200,
        "phase4_2024H1_forced_regime_trade_level.csv": 1167,
    }
    observed_rows = {}
    for name, expected in expected_rows.items():
        observed = csv_rows(RESULTS / name)
        assert observed == expected, (name, observed, expected)
        observed_rows[name] = observed

    forced_path = RESULTS / "phase4_2024H1_forced_regime_trade_level.csv"
    with forced_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["fold"] for row in rows} == {"2024 H1"}
    assert {row["policy"] for row in rows} == {"regime_forced"}
    assert all(1704067200000 <= int(row["entry_timestamp_ms"]) < 1719792000000 for row in rows)

    report = (ROOT / "PHASE4_1_DIAGNOSTIC_AMENDMENT.md").read_text(encoding="utf-8")
    assert "No regime–threshold cell was tradable under the frozen 1:1" in report
    assert "This permission is not a claim of profitability" in report
    assert "Every one of the 1,167 forced-regime trades" in report

    return {
        "phase4_trade_rows_read": results["phase4_trade_rows_read"],
        "training_cells_decomposed": results["training_cells_decomposed"],
        "forced_2024_h1_trade_rows": len(rows),
        "locked_2025_2026_rows_read": results["locked_2025_2026_price_rows_read"],
        "phase5_permission": results["phase5_permission_gate"]["phase5_permitted"],
        "csv_row_counts": observed_rows,
    }


def build_manifest(semantics: dict) -> dict:
    for path in ARTIFACTS + SOURCE_INPUTS:
        assert path.is_file(), path
    return {
        "checkpoint": "Phase 4.1 Diagnostic Amendment",
        "purpose": "Diagnostic-only amendment; Phase 4 trades and policy selection unchanged",
        "semantic_checks": semantics,
        "artifact_hashes_sha256": {
            str(path.relative_to(PROJECT)): sha256(path) for path in ARTIFACTS
        },
        "frozen_source_hashes_sha256": {
            str(path.relative_to(PROJECT)): sha256(path) for path in SOURCE_INPUTS
        },
        "manifest_self_hash_excluded": True,
    }


def verify_manifest(manifest: dict) -> None:
    for group in ("artifact_hashes_sha256", "frozen_source_hashes_sha256"):
        for relative, expected in manifest[group].items():
            path = PROJECT / relative
            assert path.is_file(), path
            observed = sha256(path)
            assert observed == expected, (relative, observed, expected)


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

    print("Phase 4.1 checkpoint: PASS")
    print(json.dumps(semantics, indent=2))


if __name__ == "__main__":
    main()
