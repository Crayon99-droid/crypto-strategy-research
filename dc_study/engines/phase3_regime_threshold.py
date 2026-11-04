#!/usr/bin/env python3
"""Causal regime-to-threshold response study for verified Bybit data.

This phase deliberately does not predict expected value.  It asks whether a
realized threshold response can be identified inside a market regime using
only information available before the next walk-forward period.  If no
threshold clears the frozen evidence gate, the regime maps to no trade.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import statistics
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


WEEK_MS = 7 * 24 * 60 * 60 * 1000
MINUTE_MS = 60_000
Z90 = 1.2815515655446004
Z95 = 1.959963984540054


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, allow_nan=False, default=json_default) + "\n",
        encoding="utf-8",
    )


def json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def to_records_clean(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if pd.isna(value):
                clean[key] = None
            elif isinstance(value, np.generic):
                clean[key] = value.item()
            else:
                clean[key] = value
        output.append(clean)
    return output


def parse_array(value: str) -> list[Any]:
    return json.loads(value)


def halfyear_label(timestamp_ms: int) -> str:
    value = pd.Timestamp(timestamp_ms, unit="ms", tz="UTC")
    return f"{value.year} H{1 if value.month <= 6 else 2}"


def audit_sources(data_dir: Path, reference_audit: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    reference = load_json(reference_audit)
    expected: dict[str, dict[str, Any]] = {}
    for section in ("candle_files", "funding_files", "instrument_files"):
        for item in reference[section]:
            expected[item["file"]] = item

    source_rows: list[dict[str, Any]] = []
    row_rows: list[dict[str, Any]] = []
    selected = [
        f"{symbol}_1m_{year}.csv.zip"
        for symbol in ("ETHUSDT", "XRPUSDT")
        for year in range(2021, 2025)
    ] + [
        "ETHUSDT_funding(1).csv",
        "XRPUSDT_funding(1).csv",
        "ETHUSDT_instrument(1).json",
        "XRPUSDT_instrument(1).json",
    ]
    for name in selected:
        path = data_dir / name
        item = expected[name]
        actual = sha256(path)
        hash_ok = actual == item["expected_sha256"]
        source = {
            "file": name,
            "sha256": actual,
            "expected_sha256": item["expected_sha256"],
            "hash_ok": hash_ok,
            "size_bytes": path.stat().st_size,
        }
        if name.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                bad_member = archive.testzip()
                members = archive.namelist()
            source.update(
                {
                    "zip_crc_ok": bad_member is None,
                    "archive_member_count": len(members),
                }
            )
            frame = pd.read_csv(path, compression="zip")
            numeric = frame[["open", "high", "low", "close", "volume_base", "turnover_usdt"]].apply(
                pd.to_numeric, errors="coerce"
            )
            ts = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
            chronology_errors = int((ts.diff().iloc[1:] != MINUTE_MS).sum())
            duplicate_timestamps = int(ts.duplicated().sum())
            finite_ok = bool(np.isfinite(numeric.to_numpy(dtype=float)).all())
            ohlc_ok = bool(
                (
                    (numeric["high"] >= numeric[["open", "close"]].max(axis=1))
                    & (numeric["low"] <= numeric[["open", "close"]].min(axis=1))
                    & (numeric["high"] >= numeric["low"])
                ).all()
            )
            row_count_ok = len(frame) == int(item["expected_rows"])
            first = pd.to_datetime(int(ts.iloc[0]), unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ")
            last = pd.to_datetime(int(ts.iloc[-1]), unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ")
            coverage_ok = first == item["first_timestamp_utc"] and last == item["last_timestamp_utc"]
            row = {
                "file": name,
                "rows": len(frame),
                "expected_rows": int(item["expected_rows"]),
                "row_count_ok": row_count_ok,
                "chronology_errors": chronology_errors,
                "duplicate_timestamps": duplicate_timestamps,
                "numeric_ok": finite_ok,
                "ohlc_ok": ohlc_ok,
                "coverage_ok": coverage_ok,
                "passed": bool(
                    hash_ok
                    and bad_member is None
                    and len(members) == 1
                    and row_count_ok
                    and chronology_errors == 0
                    and duplicate_timestamps == 0
                    and finite_ok
                    and ohlc_ok
                    and coverage_ok
                ),
            }
            row_rows.append(row)
        else:
            source.update({"zip_crc_ok": None, "archive_member_count": None})
        source_rows.append(source)
    source_frame = pd.DataFrame(source_rows)
    row_frame = pd.DataFrame(row_rows)
    if not bool(source_frame["hash_ok"].all()):
        raise RuntimeError("Source hash audit failed")
    if not bool(row_frame["passed"].all()):
        raise RuntimeError("Candle row audit failed")
    return source_frame, row_frame


def load_candidate_features(database: Path) -> pd.DataFrame:
    connection = sqlite3.connect(database)
    try:
        frame = pd.read_sql_query(
            """
            SELECT candidate_id,symbol,detector_threshold_bp AS threshold_bp,side,
                   signal_timestamp_ms,entry_timestamp_ms,available_minutes,
                   rv240_bp,return240_bp
            FROM candidates
            ORDER BY symbol,signal_timestamp_ms,candidate_id
            """,
            connection,
        )
    finally:
        connection.close()
    frame["day"] = pd.to_datetime(frame["signal_timestamp_ms"], unit="ms", utc=True).dt.floor("D")
    return frame


def raw_daily_volatility(data_dir: Path, symbols: Iterable[str]) -> pd.Series:
    """Median RV240 for every completed UTC day from every one-minute candle."""
    parts: list[pd.Series] = []
    for symbol in symbols:
        yearly: list[pd.DataFrame] = []
        for year in range(2021, 2025):
            yearly.append(
                pd.read_csv(
                    data_dir / f"{symbol}_1m_{year}.csv.zip",
                    compression="zip",
                    usecols=["timestamp_ms", "close"],
                )
            )
        bars = pd.concat(yearly, ignore_index=True)
        log_return = np.log(bars["close"].astype(float)).diff()
        bars["rv240_bp"] = (
            log_return.rolling(240, min_periods=240).std(ddof=1)
            * math.sqrt(240)
            * 10_000.0
        )
        bars["day"] = pd.to_datetime(bars["timestamp_ms"], unit="ms", utc=True).dt.floor("D")
        daily = bars.groupby("day", sort=True)["rv240_bp"].median()
        daily.index = pd.MultiIndex.from_product([[symbol], daily.index], names=["symbol", "day"])
        parts.append(daily)
    return pd.concat(parts).sort_index()


def classify_regimes(
    candidates: pd.DataFrame,
    config: dict[str, Any],
    data_dir: Path,
) -> pd.DataFrame:
    regime = config["regime"]
    q_low, q_high = regime["volatility_quantiles"]
    lookback = int(regime["volatility_lookback_days"])
    minimum = int(regime["volatility_min_history_days"])
    short_days = int(regime["volatility_transition_short_days"])
    long_days = int(regime["volatility_transition_long_days"])

    daily_parts: list[pd.DataFrame] = []
    # Regime volatility is computed from all genuine one-minute candles, not
    # from the irregularly sampled reversal candidates.  Otherwise active
    # periods receive disproportionate weight and regime labels shift.
    daily_all = raw_daily_volatility(data_dir, config["symbols"])
    for symbol, series in daily_all.groupby(level=0):
        series = series.droplevel(0).sort_index()
        prior = series.shift(1)
        trailing = prior.rolling(lookback, min_periods=minimum)
        low_cut = trailing.quantile(float(q_low))
        high_cut = trailing.quantile(float(q_high))
        short_med = prior.rolling(short_days, min_periods=short_days).median()
        long_med = prior.rolling(long_days, min_periods=long_days).median()
        ratio = short_med / long_med
        part = pd.DataFrame(
            {
                "symbol": symbol,
                "day": series.index,
                "prior_day_rv240_bp": prior.to_numpy(),
                "vol_low_cut_bp": low_cut.to_numpy(),
                "vol_high_cut_bp": high_cut.to_numpy(),
                "vol_transition_ratio": ratio.to_numpy(),
            }
        )
        part["vol_level"] = np.select(
            [
                part["prior_day_rv240_bp"] < part["vol_low_cut_bp"],
                part["prior_day_rv240_bp"] > part["vol_high_cut_bp"],
            ],
            ["low", "high"],
            default="normal",
        )
        missing = part[["prior_day_rv240_bp", "vol_low_cut_bp", "vol_high_cut_bp"]].isna().any(axis=1)
        part.loc[missing, "vol_level"] = None
        part["vol_transition"] = np.select(
            [
                part["vol_transition_ratio"] < float(regime["volatility_contracting_ratio"]),
                part["vol_transition_ratio"] > float(regime["volatility_expanding_ratio"]),
            ],
            ["contracting", "expanding"],
            default="stable",
        )
        part.loc[part["vol_transition_ratio"].isna(), "vol_transition"] = None
        daily_parts.append(part)
    daily = pd.concat(daily_parts, ignore_index=True)

    unique = candidates[["symbol", "signal_timestamp_ms", "rv240_bp", "return240_bp"]].drop_duplicates(
        ["symbol", "signal_timestamp_ms"]
    )
    unique = unique.sort_values(["symbol", "signal_timestamp_ms"]).reset_index(drop=True)
    unique["trend_score"] = unique["return240_bp"] / unique["rv240_bp"]
    entry = float(regime["trend_entry_abs"])
    exit_level = float(regime["trend_exit_abs"])
    states: list[str] = []
    for _, group in unique.groupby("symbol", sort=False):
        state = "range"
        for score in group["trend_score"].to_numpy(dtype=float):
            if not math.isfinite(score):
                states.append(state)
                continue
            if state == "bull":
                if score <= -entry:
                    state = "bear"
                elif score <= exit_level:
                    state = "range"
            elif state == "bear":
                if score >= entry:
                    state = "bull"
                elif score >= -exit_level:
                    state = "range"
            else:
                if score >= entry:
                    state = "bull"
                elif score <= -entry:
                    state = "bear"
            states.append(state)
    unique["trend_state"] = states

    output = candidates.merge(daily, on=["symbol", "day"], how="left")
    output = output.merge(
        unique[["symbol", "signal_timestamp_ms", "trend_score", "trend_state"]],
        on=["symbol", "signal_timestamp_ms"],
        how="left",
    )
    usable = (
        output["available_minutes"].ge(240)
        & output["vol_level"].notna()
        & output["vol_transition"].notna()
        & output["trend_state"].notna()
    )
    output = output.loc[usable].copy()
    output["regime_primary"] = output["vol_level"] + "_" + output["trend_state"]
    output["regime_detail"] = output["regime_primary"] + "_" + output["vol_transition"]
    return output


def funding_net_bp(side: int, exit_minute: int, minutes: list[int], rates: list[float]) -> float:
    paid = sum(float(rate) for minute, rate in zip(minutes, rates) if int(minute) <= exit_minute)
    return -int(side) * paid * 10_000.0


def create_sample_database(
    event_database: Path,
    sample_database: Path,
    classified: pd.DataFrame,
    config: dict[str, Any],
) -> None:
    if sample_database.exists():
        sample_database.unlink()
    source = sqlite3.connect(event_database)
    target = sqlite3.connect(sample_database)
    target.execute("PRAGMA journal_mode=OFF")
    target.execute("PRAGMA synchronous=OFF")
    target.execute("PRAGMA temp_store=MEMORY")
    target.execute(
        """
        CREATE TABLE samples (
            candidate_id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            threshold_bp INTEGER NOT NULL,
            side INTEGER NOT NULL,
            signal_timestamp_ms INTEGER NOT NULL,
            entry_timestamp_ms INTEGER NOT NULL,
            exit_timestamp_ms INTEGER NOT NULL,
            busy_until_ms INTEGER NOT NULL,
            exit_minutes INTEGER NOT NULL,
            week_cluster INTEGER NOT NULL,
            halfyear TEXT NOT NULL,
            vol_level TEXT NOT NULL,
            trend_state TEXT NOT NULL,
            vol_transition TEXT NOT NULL,
            regime_primary TEXT NOT NULL,
            regime_detail TEXT NOT NULL,
            rv240_bp REAL,
            return240_bp REAL,
            trend_score REAL,
            outcome TEXT NOT NULL,
            target_actual_bp REAL NOT NULL,
            stop_actual_bp REAL NOT NULL,
            net_return_bp REAL NOT NULL,
            ambiguous_same_minute INTEGER NOT NULL
        )
        """
    )
    target.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
    source_metadata = {
        key: json.loads(value)
        for key, value in source.execute("SELECT key,value_json FROM metadata")
    }
    barriers = [float(value) for value in source_metadata["barrier_levels_bp"]]
    barrier_index = {round(value, 8): index for index, value in enumerate(barriers)}
    features = classified.set_index("candidate_id")[
        [
            "regime_primary",
            "regime_detail",
            "vol_level",
            "trend_state",
            "vol_transition",
            "rv240_bp",
            "return240_bp",
            "trend_score",
        ]
    ].to_dict(orient="index")
    allowed = set(features)
    fee_bp = 2.0 * float(config["execution"]["taker_fee_bp_per_fill"])
    exit_slippage = float(config["execution"]["exit_slippage_bp"])
    query = """
        SELECT candidate_id,symbol,detector_threshold_bp,side,
               signal_timestamp_ms,entry_timestamp_ms,
               favorable_first_minutes_json,adverse_first_minutes_json,
               favorable_barriers_actual_bp_json,adverse_barriers_actual_bp_json,
               close_return_horizons_bp_json,funding_minutes_json,funding_rates_json
        FROM candidates
        ORDER BY candidate_id
    """
    insert = "INSERT INTO samples VALUES ({})".format(",".join("?" for _ in range(24)))
    rows: list[tuple[Any, ...]] = []
    processed = 0
    for row in source.execute(query):
        candidate_id = int(row[0])
        if candidate_id not in allowed:
            continue
        (
            _, symbol, threshold, side, signal_ms, entry_ms, favorable_json,
            adverse_json, favorable_actual_json, adverse_actual_json,
            close_json, funding_minutes_json, funding_rates_json,
        ) = row
        threshold = int(threshold)
        index = barrier_index[round(float(threshold), 8)]
        favorable = parse_array(favorable_json)
        adverse = parse_array(adverse_json)
        favorable_actual = parse_array(favorable_actual_json)
        adverse_actual = parse_array(adverse_actual_json)
        favorable_minute = int(favorable[index])
        adverse_minute = int(adverse[index])
        hit_favorable = 0 < favorable_minute <= 240
        hit_adverse = 0 < adverse_minute <= 240
        tie = bool(hit_favorable and hit_adverse and favorable_minute == adverse_minute)
        if hit_adverse and (not hit_favorable or adverse_minute <= favorable_minute):
            outcome = "sl_first"
            gross = -float(adverse_actual[index])
            exit_minute = adverse_minute
        elif hit_favorable:
            outcome = "tp_first"
            gross = float(favorable_actual[index])
            exit_minute = favorable_minute
        else:
            outcome = "timeout"
            gross = float(parse_array(close_json)[-1])
            exit_minute = 240
        funding = funding_net_bp(
            int(side), exit_minute, parse_array(funding_minutes_json), parse_array(funding_rates_json)
        )
        net = gross - fee_bp - exit_slippage + funding
        exit_ms = int(entry_ms) + (exit_minute - 1) * MINUTE_MS
        busy_until = int(entry_ms) + exit_minute * MINUTE_MS
        info = features[candidate_id]
        rows.append(
            (
                candidate_id, symbol, threshold, int(side), int(signal_ms), int(entry_ms),
                exit_ms, busy_until, exit_minute, int(entry_ms) // WEEK_MS,
                halfyear_label(int(entry_ms)), info["vol_level"], info["trend_state"],
                info["vol_transition"], info["regime_primary"], info["regime_detail"],
                info["rv240_bp"], info["return240_bp"], info["trend_score"], outcome,
                float(favorable_actual[index]), float(adverse_actual[index]), float(net), int(tie),
            )
        )
        if len(rows) >= 10_000:
            target.executemany(insert, rows)
            target.commit()
            processed += len(rows)
            rows.clear()
            if processed % 100_000 == 0:
                print(f"stored {processed:,} regime samples", flush=True)
    if rows:
        target.executemany(insert, rows)
        processed += len(rows)
    target.execute("CREATE INDEX idx_samples_entry ON samples(symbol,entry_timestamp_ms)")
    target.execute("CREATE INDEX idx_samples_primary ON samples(symbol,regime_primary,threshold_bp,entry_timestamp_ms)")
    target.execute("CREATE INDEX idx_samples_detail ON samples(symbol,regime_detail,threshold_bp,entry_timestamp_ms)")
    target.execute(
        "INSERT INTO metadata VALUES (?,?)",
        ("summary", json.dumps({"created_utc": utc_now(), "rows": processed, "locked_rows_read": 0})),
    )
    target.commit()
    source.close()
    target.close()


def load_samples(sample_database: Path) -> pd.DataFrame:
    connection = sqlite3.connect(sample_database)
    try:
        frame = pd.read_sql_query("SELECT * FROM samples", connection)
    finally:
        connection.close()
    return frame


def select_nonoverlap(frame: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values(group_columns + ["entry_timestamp_ms", "candidate_id"])
    keep: list[int] = []
    for _, group in ordered.groupby(group_columns, sort=False, observed=True):
        busy_until = -1
        for index, entry, until in zip(
            group.index,
            group["entry_timestamp_ms"].to_numpy(dtype=np.int64),
            group["busy_until_ms"].to_numpy(dtype=np.int64),
        ):
            if int(entry) < busy_until:
                continue
            keep.append(index)
            busy_until = int(until)
    return frame.loc[keep].sort_values(group_columns + ["entry_timestamp_ms", "candidate_id"]).copy()


def cluster_metrics(group: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    n = len(group)
    values = group["net_return_bp"].to_numpy(dtype=float)
    mean = float(values.mean()) if n else math.nan
    positive = values[values > 0]
    negative = -values[values < 0]
    profit_factor = float(positive.sum() / negative.sum()) if negative.size and negative.sum() > 0 else None
    weekly = group.groupby("week_cluster", sort=False)["net_return_bp"].agg(["count", "sum"])
    clusters = len(weekly)
    if n > 1 and clusters > 1:
        residual = weekly["sum"].to_numpy(dtype=float) - weekly["count"].to_numpy(dtype=float) * mean
        cluster_se = math.sqrt(clusters / (clusters - 1) * float(np.square(residual).sum())) / n
    else:
        cluster_se = math.nan
    if math.isfinite(cluster_se) and cluster_se > 0:
        z = mean / cluster_se
        one_sided_p = 0.5 * math.erfc(z / math.sqrt(2.0))
    elif mean > 0:
        one_sided_p = 0.0
    else:
        one_sided_p = 1.0
    minimum_halfyear = int(config["threshold_evidence_gate"]["minimum_trades_per_evaluated_halfyear"])
    halfyear = group.groupby("halfyear")["net_return_bp"].agg(["count", "mean"])
    evaluated = halfyear.loc[halfyear["count"] >= minimum_halfyear]
    positive_halfyears = int((evaluated["mean"] > 0).sum())
    positive_fraction = positive_halfyears / len(evaluated) if len(evaluated) else 0.0
    result = {
        "observations": n,
        "mean_net_return_bp": mean if n else None,
        "median_net_return_bp": float(np.median(values)) if n else None,
        "net_positive_rate": float((values > 0).mean()) if n else None,
        "tp_first_rate": float((group["outcome"] == "tp_first").mean()) if n else None,
        "profit_factor": profit_factor,
        "weekly_clusters": clusters,
        "weekly_cluster_se_bp": cluster_se if math.isfinite(cluster_se) else None,
        "weekly_lower_90_bp": mean - Z90 * cluster_se if math.isfinite(cluster_se) else None,
        "weekly_lower_95_bp": mean - Z95 * cluster_se if math.isfinite(cluster_se) else None,
        "one_sided_p": one_sided_p,
        "positive_halfyears": positive_halfyears,
        "evaluated_halfyears": int(len(evaluated)),
        "positive_halfyear_fraction": positive_fraction,
    }
    gate = config["threshold_evidence_gate"]
    result["evidence_gate_pass"] = bool(
        n >= int(gate["minimum_nonoverlap_trades"])
        and clusters >= int(gate["minimum_weekly_clusters"])
        and mean > 0
        and profit_factor is not None
        and profit_factor > float(gate["profit_factor_above"])
        and result["weekly_lower_90_bp"] is not None
        and result["weekly_lower_90_bp"] > 0
        and positive_fraction >= float(gate["minimum_positive_halfyear_fraction"])
    )
    return result


def bh_qvalues(pvalues: Iterable[float]) -> np.ndarray:
    p = np.asarray(list(pvalues), dtype=float)
    n = len(p)
    order = np.argsort(p)
    ranked = p[order]
    adjusted = ranked * n / np.arange(1, n + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    adjusted = np.clip(adjusted, 0.0, 1.0)
    output = np.empty(n, dtype=float)
    output[order] = adjusted
    return output


def response_table(frame: pd.DataFrame, regime_column: str, config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    economic = select_nonoverlap(frame, ["symbol", regime_column, "threshold_bp"])
    records: list[dict[str, Any]] = []
    for keys, group in economic.groupby(["symbol", regime_column, "threshold_bp"], sort=True, observed=True):
        symbol, regime, threshold = keys
        records.append(
            {
                "symbol": symbol,
                "regime": regime,
                "threshold_bp": int(threshold),
                **cluster_metrics(group, config),
            }
        )
    response = pd.DataFrame(records)
    if not response.empty:
        response["bh_q"] = bh_qvalues(response["one_sided_p"].fillna(1.0))
    return response, economic


def hindsight_winners(samples: pd.DataFrame, config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    economic = select_nonoverlap(samples, ["symbol", "halfyear", "regime_primary", "threshold_bp"])
    rows: list[dict[str, Any]] = []
    for keys, group in economic.groupby(
        ["symbol", "halfyear", "regime_primary", "threshold_bp"], sort=True, observed=True
    ):
        symbol, halfyear, regime, threshold = keys
        rows.append(
            {
                "symbol": symbol,
                "halfyear": halfyear,
                "regime": regime,
                "threshold_bp": int(threshold),
                **cluster_metrics(group, config),
            }
        )
    cells = pd.DataFrame(rows)
    # Do not let a threshold with only a handful of trades become the
    # "hindsight winner" merely through variance.  Thirty non-overlapping
    # trades is still exploratory, but it is a defensible descriptive floor.
    descriptive_minimum = int(
        config["threshold_evidence_gate"]["minimum_trades_per_evaluated_halfyear"]
    )
    eligible = cells.loc[cells["observations"] >= descriptive_minimum]
    winners = (
        eligible.sort_values(
            ["symbol", "regime", "halfyear", "mean_net_return_bp", "observations", "threshold_bp"],
            ascending=[True, True, True, False, False, True],
        )
        .groupby(["symbol", "regime", "halfyear"], as_index=False, observed=True)
        .head(1)
        .sort_values(["symbol", "regime", "halfyear"])
        .reset_index(drop=True)
    )
    return winners, cells


def choose_threshold_map(
    response: pd.DataFrame,
    evidence: bool,
    minimum_observations: int = 100,
) -> dict[tuple[str, str], int]:
    eligible = response.loc[response["observations"] >= minimum_observations].copy()
    if evidence:
        eligible = eligible.loc[eligible["evidence_gate_pass"]]
    if eligible.empty:
        return {}
    chosen = (
        eligible.sort_values(
            ["symbol", "regime", "mean_net_return_bp", "weekly_lower_90_bp", "observations"],
            ascending=[True, True, False, False, False],
        )
        .groupby(["symbol", "regime"], observed=True)
        .head(1)
    )
    return {(row.symbol, row.regime): int(row.threshold_bp) for row in chosen.itertuples()}


def simulate_policy(
    test: pd.DataFrame,
    threshold_map: dict[tuple[str, str], int] | int,
    regime_column: str | None,
    policy: str,
    fold: str,
) -> pd.DataFrame:
    selected: list[pd.Series] = []
    for symbol, group in test.groupby("symbol", sort=True):
        busy_until = -1
        ordered = group.sort_values(["entry_timestamp_ms", "candidate_id"])
        for _, row in ordered.iterrows():
            if int(row.entry_timestamp_ms) < busy_until:
                continue
            if regime_column is None:
                required = int(threshold_map)
            else:
                required = threshold_map.get((symbol, str(row[regime_column])))
                if required is None:
                    continue
            if int(row.threshold_bp) != int(required):
                continue
            selected.append(row)
            busy_until = int(row.busy_until_ms)
    if not selected:
        return pd.DataFrame(columns=list(test.columns) + ["policy", "fold"])
    output = pd.DataFrame(selected)
    output["policy"] = policy
    output["fold"] = fold
    return output


def fold_metrics(trades: pd.DataFrame, config: dict[str, Any], policy: str, fold: str) -> dict[str, Any]:
    if trades.empty:
        return {
            "policy": policy,
            "fold": fold,
            "trades": 0,
            "mean_net_return_bp": None,
            "total_net_return_bp": 0.0,
            "profit_factor": None,
            "positive": False,
        }
    metrics = cluster_metrics(trades, config)
    return {
        "policy": policy,
        "fold": fold,
        "trades": len(trades),
        "mean_net_return_bp": metrics["mean_net_return_bp"],
        "total_net_return_bp": float(trades["net_return_bp"].sum()),
        "profit_factor": metrics["profit_factor"],
        "weekly_lower_95_bp": metrics["weekly_lower_95_bp"],
        "positive": bool(metrics["mean_net_return_bp"] > 0),
    }


def walk_forward(samples: pd.DataFrame, config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    folds = config["walk_forward_folds"]
    thresholds = [int(value) for value in config["detector_thresholds_bp"]]
    all_trades: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    map_rows: list[dict[str, Any]] = []
    for fold in folds:
        start = int(pd.Timestamp(fold["start"]).timestamp() * 1000)
        end = int(pd.Timestamp(fold["end"]).timestamp() * 1000)
        training = samples.loc[samples["entry_timestamp_ms"] < start - 240 * MINUTE_MS]
        test = samples.loc[
            (samples["entry_timestamp_ms"] >= start) & (samples["entry_timestamp_ms"] < end)
        ]
        primary_response, _ = response_table(training, "regime_primary", config)
        detail_response, _ = response_table(training, "regime_detail", config)
        primary_evidence = choose_threshold_map(primary_response, evidence=True)
        detail_evidence = choose_threshold_map(detail_response, evidence=True)
        primary_forced = choose_threshold_map(primary_response, evidence=False)
        policies = [
            ("R9_evidence", primary_evidence, "regime_primary"),
            ("R27_evidence", detail_evidence, "regime_detail"),
            ("R9_forced", primary_forced, "regime_primary"),
        ]
        for policy, mapping, regime_column in policies:
            for (symbol, regime), threshold in sorted(mapping.items()):
                map_rows.append(
                    {
                        "fold": fold["name"],
                        "policy": policy,
                        "symbol": symbol,
                        "regime": regime,
                        "threshold_bp": threshold,
                    }
                )
            trades = simulate_policy(test, mapping, regime_column, policy, fold["name"])
            all_trades.append(trades)
            fold_rows.append(fold_metrics(trades, config, policy, fold["name"]))
        for threshold in thresholds:
            policy = f"fixed_{threshold:03d}"
            trades = simulate_policy(test, threshold, None, policy, fold["name"])
            all_trades.append(trades)
            fold_rows.append(fold_metrics(trades, config, policy, fold["name"]))
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    folds_frame = pd.DataFrame(fold_rows)
    maps_frame = pd.DataFrame(map_rows)
    aggregate_rows: list[dict[str, Any]] = []
    for policy, group in trades.groupby("policy", sort=True):
        metrics = cluster_metrics(group, config)
        fold_group = folds_frame.loc[folds_frame["policy"] == policy]
        values = group["net_return_bp"].to_numpy(dtype=float)
        cumulative = np.cumsum(values)
        peaks = np.maximum.accumulate(np.r_[0.0, cumulative])[:-1]
        drawdown = peaks - cumulative
        aggregate_rows.append(
            {
                "policy": policy,
                "trades": len(group),
                "mean_net_return_bp": metrics["mean_net_return_bp"],
                "total_net_return_bp": float(values.sum()),
                "profit_factor": metrics["profit_factor"],
                "max_drawdown_bp": float(drawdown.max()) if len(drawdown) else 0.0,
                "weekly_clusters": metrics["weekly_clusters"],
                "weekly_lower_95_bp": metrics["weekly_lower_95_bp"],
                "positive_folds": int(fold_group["positive"].sum()),
                "evaluated_folds": int(len(fold_group)),
            }
        )
    aggregate = pd.DataFrame(aggregate_rows).sort_values("policy").reset_index(drop=True)
    return trades, folds_frame, maps_frame, aggregate


def phase2_diagnostics(
    phase2_trades_path: Path,
    classified: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    trades = pd.read_csv(phase2_trades_path)
    trades["signal_timestamp_ms"] = (
        pd.to_datetime(trades["signal_timestamp_utc"], utc=True).astype("int64") // 1_000_000
    )
    regime_lookup = classified[
        [
            "symbol", "signal_timestamp_ms", "vol_level", "trend_state",
            "vol_transition", "regime_primary", "regime_detail",
        ]
    ].drop_duplicates(["symbol", "signal_timestamp_ms"])
    annotated = trades.merge(regime_lookup, on=["symbol", "signal_timestamp_ms"], how="left")

    def side_summary(group_columns: list[str]) -> pd.DataFrame:
        records = []
        for keys, group in annotated.groupby(group_columns, dropna=False, sort=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            values = group["net_return_bp"].to_numpy(dtype=float)
            positive = values[values > 0].sum()
            negative = -values[values < 0].sum()
            record = dict(zip(group_columns, keys))
            record.update(
                {
                    "trades": len(group),
                    "mean_net_return_bp": float(values.mean()),
                    "total_net_return_bp": float(values.sum()),
                    "net_positive_rate": float((values > 0).mean()),
                    "profit_factor": float(positive / negative) if negative > 0 else None,
                    "tp_first_rate": float((group["outcome"] == "tp_first").mean()),
                    "sl_first_rate": float((group["outcome"] == "sl_first").mean()),
                }
            )
            records.append(record)
        return pd.DataFrame(records)

    return annotated, side_summary(["side_name"]), side_summary(["side_name", "trend_state", "vol_transition"])


def self_test() -> None:
    p = bh_qvalues([0.01, 0.02, 0.5])
    assert np.allclose(p, [0.03, 0.03, 0.5])
    test = pd.DataFrame(
        {
            "symbol": ["X", "X", "X"],
            "regime_primary": ["r", "r", "r"],
            "threshold_bp": [25, 25, 25],
            "entry_timestamp_ms": [0, 60_000, 180_000],
            "busy_until_ms": [120_000, 180_000, 240_000],
            "candidate_id": [1, 2, 3],
        }
    )
    selected = select_nonoverlap(test, ["symbol", "regime_primary", "threshold_bp"])
    assert selected["candidate_id"].tolist() == [1, 3]
    assert halfyear_label(1704067200000) == "2024 H1"
    print("self-test: PASS")


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = load_json(args.config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source_audit, row_audit = audit_sources(args.data_dir, args.reference_audit)
    source_audit.to_csv(args.output_dir / "phase3_source_audit.csv", index=False)
    row_audit.to_csv(args.output_dir / "phase3_row_audit.csv", index=False)

    connection = sqlite3.connect(args.event_database)
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    event_rows = connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    locked_rows = connection.execute(
        "SELECT COUNT(*) FROM candidates WHERE signal_timestamp_ms >= ?",
        (int(pd.Timestamp("2025-01-01T00:00:00Z").timestamp() * 1000),),
    ).fetchone()[0]
    connection.close()
    if integrity != "ok" or locked_rows != 0:
        raise RuntimeError("Event database integrity/leakage check failed")

    candidates = load_candidate_features(args.event_database)
    classified = classify_regimes(candidates, config, args.data_dir)
    print(f"causal regime samples: {len(classified):,}", flush=True)
    create_sample_database(args.event_database, args.sample_database, classified, config)
    samples = load_samples(args.sample_database)

    primary_response, _ = response_table(samples, "regime_primary", config)
    detail_response, _ = response_table(samples, "regime_detail", config)
    winners, _ = hindsight_winners(samples, config)
    trades, fold_summary, maps, aggregate = walk_forward(samples, config)
    annotated, phase2_side, phase2_regime_side = phase2_diagnostics(args.phase2_trades, classified)

    primary_response.to_csv(args.output_dir / "phase3_primary_regime_threshold_response.csv", index=False)
    detail_response.to_csv(args.output_dir / "phase3_detail_regime_threshold_response.csv", index=False)
    winners.to_csv(args.output_dir / "phase3_halfyear_hindsight_winners.csv", index=False)
    trades.to_csv(args.output_dir / "phase3_walkforward_trades.csv", index=False)
    fold_summary.to_csv(args.output_dir / "phase3_walkforward_fold_summary.csv", index=False)
    maps.to_csv(args.output_dir / "phase3_walkforward_threshold_maps.csv", index=False)
    aggregate.to_csv(args.output_dir / "phase3_walkforward_aggregate.csv", index=False)
    annotated.to_csv(args.output_dir / "phase2_2024_regime_annotated_trades.csv", index=False)
    phase2_side.to_csv(args.output_dir / "phase2_2024_side_summary.csv", index=False)
    phase2_regime_side.to_csv(args.output_dir / "phase2_2024_regime_side_summary.csv", index=False)

    winners = winners.sort_values(["symbol", "regime", "halfyear"])
    adjacent_differences: list[int] = []
    exact = 0
    within25 = 0
    within50 = 0
    comparisons = 0
    for _, group in winners.groupby(["symbol", "regime"], sort=True):
        values = group["threshold_bp"].to_numpy(dtype=int)
        for left, right in zip(values[:-1], values[1:]):
            difference = abs(int(right) - int(left))
            adjacent_differences.append(difference)
            comparisons += 1
            exact += int(difference == 0)
            within25 += int(difference <= 25)
            within50 += int(difference <= 50)

    phase2_short = annotated.loc[annotated["side_name"] == "short"]
    short_expanding = phase2_short.loc[phase2_short["vol_transition"] == "expanding"]
    total_short_net_loss = -float(phase2_short["net_return_bp"].sum())
    expanding_short_net_loss = -float(short_expanding["net_return_bp"].sum())

    result = {
        "created_utc": utc_now(),
        "decision": "REJECT_CURRENT_REGIME_THRESHOLD_MAP",
        "data": {
            "source_files_checked": int(len(source_audit)),
            "raw_candles": int(row_audit["rows"].sum()),
            "event_candidates": int(event_rows),
            "usable_regime_samples": int(len(samples)),
            "event_database_integrity": integrity,
            "sample_database_integrity": sqlite3.connect(args.sample_database).execute("PRAGMA integrity_check").fetchone()[0],
            "locked_2025_2026_rows_read": int(locked_rows),
        },
        "hindsight": {
            "regime_halfyear_cells": int(len(winners)),
            "positive_best_threshold_cells": int((winners["mean_net_return_bp"] > 0).sum()),
            "positive_best_threshold_fraction": float((winners["mean_net_return_bp"] > 0).mean()),
            "weekly_lower90_positive_cells": int((winners["weekly_lower_90_bp"] > 0).sum()),
            "thresholds_that_won_somewhere": int(winners["threshold_bp"].nunique()),
            "adjacent_comparisons": comparisons,
            "adjacent_exact_repeat": exact,
            "adjacent_exact_repeat_fraction": exact / comparisons if comparisons else None,
            "adjacent_within25_fraction": within25 / comparisons if comparisons else None,
            "adjacent_within50_fraction": within50 / comparisons if comparisons else None,
            "median_adjacent_shift_bp": float(np.median(adjacent_differences)) if adjacent_differences else None,
            "mean_adjacent_shift_bp": float(np.mean(adjacent_differences)) if adjacent_differences else None,
        },
        "multiplicity": {
            "primary_rows": int(len(primary_response)),
            "primary_evidence_gate_rows": int(primary_response["evidence_gate_pass"].sum()),
            "primary_bh_q_below_0_10": int((primary_response["bh_q"] < 0.10).sum()),
            "detail_rows": int(len(detail_response)),
            "detail_evidence_gate_rows": int(detail_response["evidence_gate_pass"].sum()),
            "detail_bh_q_below_0_10": int((detail_response["bh_q"] < 0.10).sum()),
        },
        "walk_forward": to_records_clean(aggregate),
        "walk_forward_folds": to_records_clean(fold_summary),
        "phase2_side": to_records_clean(phase2_side),
        "phase2_short_expanding": {
            "trades": int(len(short_expanding)),
            "mean_net_return_bp": float(short_expanding["net_return_bp"].mean()),
            "total_net_return_bp": float(short_expanding["net_return_bp"].sum()),
            "share_of_net_short_loss": (
                expanding_short_net_loss / total_short_net_loss
                if total_short_net_loss > 0
                else None
            ),
        },
        "definitions": config,
    }
    write_json(args.output_dir / "PHASE3_REGIME_THRESHOLD_RESULTS.json", result)
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--data-dir", type=Path, required=True)
    run_parser.add_argument("--reference-audit", type=Path, required=True)
    run_parser.add_argument("--config", type=Path, required=True)
    run_parser.add_argument("--event-database", type=Path, required=True)
    run_parser.add_argument("--sample-database", type=Path, required=True)
    run_parser.add_argument("--phase2-trades", type=Path, required=True)
    run_parser.add_argument("--output-dir", type=Path, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "self-test":
        self_test()
    else:
        output = run(args)
        print(json.dumps(output, indent=2, default=json_default))


if __name__ == "__main__":
    main()
