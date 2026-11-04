# Directional-change study: replication archive

Code and frozen configurations behind the working paper
[`paper/Directional_Change_Crypto_Null_Result_Tariq_2026.pdf`](../paper/Directional_Change_Crypto_Null_Result_Tariq_2026.pdf).

| Folder | Contents |
|---|---|
| `configs/` | Frozen JSON configurations and protocols for each phase: event study (Phase 1), dynamic threshold model (Phase 2), regime maps (Phases 3-5), confirmation-impulse filter (H1), BTC lead-lag filter (H2) |

Key frozen settings (from the configs): ETHUSDT and XRPUSDT; nine volatility
multipliers k = 0.25 to 3.0 applied to the previous completed UTC day's median
240-minute realised volatility; 5.5 bp taker fee per fill plus adverse slippage and
tick rounding; 240-minute maximum hold; 180-day causal lookbacks; locked test from
2025-01-01; fixed random seeds for all bootstrap procedures.

Raw exchange data is not redistributed. It can be re-downloaded from Bybit's public
endpoints with `data_tools/bybit_download.py`; the paper's Section 3 describes the
integrity checks applied.

More components (engines, derived outputs) are being added.
