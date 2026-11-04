#!/usr/bin/env python3
"""Create the final SHA-256 manifest for the 2024 validation checkpoint."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "phase2_2024_results"
FILES = [
    ROOT / "phase2_2024_validation.py",
    ROOT / "dc_validation_collection_summary.json",
    RESULTS / "PHASE2_2024_VALIDATION_RESULTS.json",
    RESULTS / "PHASE2_2024_VALIDATION_REPORT.md",
    RESULTS / "Phase2_2024_Validation_Audit.xlsx",
    RESULTS / "phase2_2024_validation_trades.csv",
    RESULTS / "phase2_2024_by_symbol.csv",
    RESULTS / "phase2_2024_by_threshold.csv",
    RESULTS / "phase2_2024_by_month.csv",
    RESULTS / "phase2_2024_score_deciles.csv",
    RESULTS / "phase2_2024_source_audit.csv",
]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    missing = [str(path) for path in FILES if not path.is_file()]
    if missing:
        raise FileNotFoundError(missing)
    payload = {
        "checkpoint": "Phase 2 frozen Candidate A — one-time 2024 validation",
        "decision": "REJECT_CANDIDATE_A",
        "files": [
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            for path in FILES
        ],
    }
    output = RESULTS / "PHASE2_2024_VALIDATION_INTEGRITY.json"
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
