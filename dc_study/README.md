# Directional-change study: replication archive

Code and frozen configurations behind the working paper
[`paper/Directional_Change_Crypto_Null_Result_Tariq_2026.pdf`](../paper/Directional_Change_Crypto_Null_Result_Tariq_2026.pdf).

## Contents

| Paper stage | Script(s) in `engines/` | Config in `configs/` |
|---|---|---|
| Data acquisition and audit | `bybit_data_downloader.py`, `create_validation_integrity_manifest.py` | - |
| Stage 0: legacy TradingView parity | `helios_backtester.py` (Pine-compatible replay of HELIOS DC v3, incl. delayed orders and partial exits) | - |
| Phase 1: fixed-threshold event study | `dc_research_engine.py` (`collect` / analysis passes, independent detector streams) | `dc_research_config.json` |
| Phase 2: dynamic threshold model + 2024 validation | `phase2_dynamic_threshold.py`, `phase2_2024_validation.py` | `phase2_dynamic_threshold_config.json` |
| Phase 3: absolute regime maps | `phase3_regime_threshold.py`, `verify_phase3_checkpoint.py` | `phase3_regime_config.json` |
| Phase 4: volatility-normalised regime maps | `phase4_regime_normalized_dc.py`, `phase4_1_diagnostics.py`, `verify_phase4_checkpoint.py`, `verify_phase4_1_checkpoint.py` | `phase4_config.json` |
| Phase 5: 1.5R revision and diagnostics | `phase5_final_rr15.py`, `phase5_diagnostics.py`, `phase5_minimum_tp_diagnostic.py`, `phase5_symmetric_close_bracket.py`, `verify_phase5_checkpoint.py` | `phase5_config.json` |
| Cost capacity (Section 5.7) | `cost_sensitivity_gate.py` | - |
| H1 confirmation-impulse filter | `confirmation_impulse_selectivity.py` | `CONFIRMATION_IMPULSE_PROTOCOL.json` |
| H2 BTC lead-lag filter | `h2_btc_lead_lag.py` | `BTC_LEAD_LAG_PROTOCOL.json` |

`reports/` holds the decision reports written at each gate: Phase 4 and its 4.1
diagnostic amendment, the Phase 5 final report, the cost-sensitivity gate, the
nine-k selection and 98% inference amendment, and the H2 BTC lead-lag report.

`verify_*` scripts re-check frozen checkpoints (hashes, row counts, deterministic
reruns) before later stages read them.

Key frozen settings: ETHUSDT and XRPUSDT; nine volatility multipliers k = 0.25 to
3.0 applied to the previous completed UTC day's median 240-minute realised
volatility; 5.5 bp taker fee per fill plus adverse slippage and tick rounding;
240-minute maximum hold; 180-day causal lookbacks; locked test from 2025-01-01;
fixed random seeds for all bootstrap procedures.

## Data

Raw exchange data is not redistributed. Re-download it from Bybit's public endpoints
with `engines/bybit_data_downloader.py` (or `../data_tools/bybit_download.py`); the
paper's Section 3 describes the integrity checks. The derived SQLite event databases
are published separately as a GitHub release (see the repository's Releases page)
because of their size; the H1 protocol records the SHA-256 of the Phase 5 candidate
archive it reads.

## Requirements

Python 3.11+, `pip install -r ../requirements.txt` (numpy, pandas; the standard
library covers sqlite3, gzip, hashlib, zipfile).
