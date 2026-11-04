#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sqlite_facts(path: Path, table: str) -> dict:
    connection = sqlite3.connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        rows = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        max_timestamp = connection.execute(
            f"SELECT MAX(signal_timestamp_ms) FROM {table}"
        ).fetchone()[0]
    finally:
        connection.close()
    return {"integrity": integrity, "rows": rows, "max_signal_timestamp_ms": max_timestamp}


def main() -> None:
    source = __import__("pandas").read_csv(RESULTS / "phase3_source_audit.csv")
    rows = __import__("pandas").read_csv(RESULTS / "phase3_row_audit.csv")
    event = sqlite_facts(ROOT / "dc_regime_development_2021_2024.sqlite", "candidates")
    samples = sqlite_facts(ROOT / "phase3_regime_samples.sqlite", "samples")
    with zipfile.ZipFile(ROOT / "Phase3_Regime_Threshold_Audit.xlsx") as archive:
        xlsx_bad_member = archive.testzip()
    result = json.loads((RESULTS / "PHASE3_REGIME_THRESHOLD_RESULTS.json").read_text())
    checks = {
        "source_hashes_all_pass": bool(source["hash_ok"].all()),
        "candle_rows_all_pass": bool(rows["passed"].all()),
        "event_database_integrity": event["integrity"] == "ok",
        "event_candidate_count": event["rows"] == 696679,
        "sample_database_integrity": samples["integrity"] == "ok",
        "sample_count": samples["rows"] == 661298,
        "no_2025_plus_event_rows": event["max_signal_timestamp_ms"] < 1735689600000,
        "no_2025_plus_sample_rows": samples["max_signal_timestamp_ms"] < 1735689600000,
        "results_locked_rows_read_zero": result["data"]["locked_2025_2026_rows_read"] == 0,
        "workbook_zip_integrity": xlsx_bad_member is None,
        "decision_is_reject": result["decision"] == "REJECT_CURRENT_REGIME_THRESHOLD_MAP",
    }
    artifacts = [
        ROOT / "phase3_regime_threshold.py",
        ROOT / "phase3_regime_config.json",
        ROOT / "PHASE3_REGIME_THRESHOLD_REPORT.md",
        ROOT / "Phase3_Regime_Threshold_Audit.xlsx",
        ROOT / "dc_regime_collection_summary.json",
        ROOT / "dc_regime_development_2021_2024.sqlite.gz",
        ROOT / "phase3_regime_samples.sqlite.gz",
        RESULTS / "PHASE3_REGIME_THRESHOLD_RESULTS.json",
        RESULTS / "phase3_source_audit.csv",
        RESULTS / "phase3_row_audit.csv",
        RESULTS / "phase3_walkforward_aggregate.csv",
        RESULTS / "phase3_walkforward_fold_summary.csv",
        RESULTS / "phase3_walkforward_threshold_maps.csv",
        RESULTS / "phase3_primary_regime_threshold_response.csv",
        RESULTS / "phase3_detail_regime_threshold_response.csv",
        RESULTS / "phase3_halfyear_hindsight_winners.csv",
        RESULTS / "phase2_2024_side_summary.csv",
        RESULTS / "phase2_2024_regime_side_summary.csv",
    ]
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "event_database": event,
        "sample_database": samples,
        "artifacts": [
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            for path in artifacts
        ],
    }
    output = ROOT / "PHASE3_INTEGRITY_MANIFEST.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    if manifest["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
