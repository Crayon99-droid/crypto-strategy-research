from pathlib import Path
import zipfile
import pandas as pd
import numpy as np

# ============================================================
# EURUSD.OOS1 — PDH/PDL STRUCTURAL DESTINATION REPLICATION
# ============================================================
#
# PURPOSE
# -------
# BTC H4.NULL1 / NULL2 led to a much narrower hypothesis:
#
#   Most first-touch predictability is geometry.
#   However, real previous-day highs/lows (PDH/PDL) may add a
#   structural effect beyond geometry and matched placement.
#
# EURUSD is now used as an EXTERNAL MARKET replication.
#
# This script does NOT tune any trading rule.
# It does NOT test GMMA or DC.
# It tests only the frozen destination hypothesis:
#
#   Do genuine EURUSD previous-session highs/lows outperform:
#     1) exact first-passage geometry, and
#     2) an exact-distance mirrored placebo
#   on the actual future EURUSD path?
#
# ============================================================
# IMPORTANT FX ADAPTATION
# ============================================================
#
# "Previous day" is defined as the previous STANDARD FX trading
# session, not the previous UTC calendar date.
#
# Session boundary:
#     17:00 America/New_York to 17:00 America/New_York
#
# This automatically handles U.S. daylight-saving time.
#
# Example:
# Sunday 17:00 NY -> Monday 17:00 NY = Monday FX session.
#
# PDH = high of previous completed FX session
# PDL = low  of previous completed FX session
#
# ============================================================
# PRICE SERIES
# ============================================================
#
# Source files are tick quotes with BID and ASK.
# For this structural/destination test, M1 MIDQUOTE bars are built:
#
#     midpoint = (bid + ask) / 2
#
# Why midpoint?
# - EURUSD has no centralized last-trade series.
# - Using midpoint avoids giving one side a systematic bid/ask bias.
# - This is a destination-structure test, not executable P&L.
#
# Missing one-sided quote updates are forward-filled causally from
# the most recently observed bid/ask.
#
# ============================================================
# EVENT DEFINITION — PREDECLARED
# ============================================================
#
# One event per eligible FX session:
#
# 1) Previous session and current session must each contain at least
#    MIN_SESSION_MINUTES distinct M1 observations. This is a fixed
#    data-quality screen, not an edge parameter.
#
# 2) At the first available M1 bar of the current FX session,
#    use its OPEN midpoint as the decision price P0.
#
# 3) P0 must be STRICTLY inside the previous session range:
#
#       PDL < P0 < PDH
#
# 4) Distances:
#
#       Du = PDH - P0
#       Dl = P0 - PDL
#
#       near/far ratio = min(Du,Dl)/max(Du,Dl)
#
# 5) Follow the actual current-session M1 path until that session ends.
#    Record whether PDH or PDL is touched first.
#
# ============================================================
# GEOMETRY NULL
# ============================================================
#
# Zero-drift first-passage probability:
#
#       P(PDH first) = Dl / (Du + Dl)
#
# Therefore:
#
#       P(nearer first) = far / (near + far)
#
# The observed near-first rate itself is NOT treated as edge.
# Only the residual above geometry matters.
#
# ============================================================
# MIRROR PLACEBO
# ============================================================
#
# Keep the EXACT same two distances and the EXACT same future path,
# but swap which side receives them:
#
#       placebo upper = P0 + Dl
#       placebo lower = P0 - Du
#
# This preserves:
# - decision time
# - real future EURUSD path
# - volatility/regime
# - exact distance pair
# - exact near/far ratio
# - first-passage geometry
#
# It destroys:
# - the actual structural placement of PDH vs PDL.
#
# Primary paired statistic:
#
#       Real nearer-first - Mirror nearer-first
#
# ============================================================
# PRIMARY PREDECLARED SAMPLE
# ============================================================
#
# Frozen from BTC:
#
#       near/far <= 0.50
#
# We also report all eligible sessions.
#
# NO threshold search is performed.
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

FILES = [
    "EURUSD_2023.zip",
    "EURUSD_2024.zip",
    "EURUSD_2025.zip",
    "EURUSD_2026.zip",
]

OUTPUT_FOLDER = SCRIPT_DIR / "EURUSD_OOS1_results"
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

M1_CACHE = SCRIPT_DIR / "EURUSD_midquote_M1.csv.gz"

CHUNK_SIZE = 1_000_000

# Fixed data-quality criterion.
# A normal FX session has roughly 1,400+ M1 bars.
# Requiring 1,000 removes truncated edge/holiday sessions.
MIN_SESSION_MINUTES = 1000

STRONG_RATIO_MAX = 0.50

BOOTSTRAP_REPS = 10000
BOOTSTRAP_SEED = 20260908


# ============================================================
# HELPERS
# ============================================================

def safe_to_csv(frame, filename):
    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_FOLDER / filename
    frame.to_csv(path, index=False)
    return path


def first_touch_from_path(
    highs,
    lows,
    upper_price,
    lower_price,
):
    upper_hits = np.flatnonzero(
        highs >= upper_price
    )

    lower_hits = np.flatnonzero(
        lows <= lower_price
    )

    iu = (
        int(upper_hits[0])
        if upper_hits.size
        else None
    )

    il = (
        int(lower_hits[0])
        if lower_hits.size
        else None
    )

    if iu is None and il is None:
        return "TIMEOUT", None

    if iu is not None and il is None:
        return "UPPER", iu

    if iu is None and il is not None:
        return "LOWER", il

    if iu < il:
        return "UPPER", iu

    if il < iu:
        return "LOWER", il

    return "AMBIGUOUS", iu


def build_m1_midquote():
    """
    Stream the large tick ZIP files and aggregate to M1 midpoint bars.
    A gzip CSV cache is saved so later studies do not need to rebuild.
    """

    all_chunk_bars = []

    last_bid = np.nan
    last_ask = np.nan

    for filename in FILES:
        zip_path = SCRIPT_DIR / filename

        if not zip_path.exists():
            raise FileNotFoundError(
                f"\nMissing EURUSD file:\n{zip_path}\n"
                "Put all four EURUSD ZIP files beside this script."
            )

        print(f"\n============================================")
        print(f"BUILDING M1 FROM {filename}")
        print(f"============================================")

        with zipfile.ZipFile(zip_path, "r") as z:
            csv_files = [
                n for n in z.namelist()
                if n.lower().endswith(".csv")
            ]

            if not csv_files:
                raise ValueError(
                    f"No CSV found in {zip_path}"
                )

            csv_name = csv_files[0]

            with z.open(csv_name) as f:
                reader = pd.read_csv(
                    f,
                    sep="\t",
                    usecols=[
                        "<DATE>",
                        "<TIME>",
                        "<BID>",
                        "<ASK>",
                    ],
                    dtype={
                        "<DATE>": "string",
                        "<TIME>": "string",
                        "<BID>": "float64",
                        "<ASK>": "float64",
                    },
                    chunksize=CHUNK_SIZE,
                )

                total_rows = 0

                for chunk_no, chunk in enumerate(
                    reader,
                    start=1,
                ):
                    total_rows += len(chunk)

                    bid = chunk["<BID>"].copy()
                    ask = chunk["<ASK>"].copy()

                    # Causal carry-forward of one-sided quote updates.
                    bid = bid.ffill()
                    ask = ask.ffill()

                    if np.isfinite(last_bid):
                        bid = bid.fillna(last_bid)

                    if np.isfinite(last_ask):
                        ask = ask.fillna(last_ask)

                    valid_bid = bid.dropna()
                    valid_ask = ask.dropna()

                    if len(valid_bid):
                        last_bid = float(
                            valid_bid.iloc[-1]
                        )

                    if len(valid_ask):
                        last_ask = float(
                            valid_ask.iloc[-1]
                        )

                    mid = (
                        bid + ask
                    ) / 2.0

                    minute_key = (
                        chunk["<DATE>"].astype("string")
                        + " "
                        + chunk["<TIME>"]
                          .astype("string")
                          .str.slice(0, 5)
                    )

                    temp = pd.DataFrame({
                        "minute_key": minute_key,
                        "mid": mid,
                    })

                    temp = temp.dropna(
                        subset=[
                            "minute_key",
                            "mid",
                        ]
                    )

                    if temp.empty:
                        continue

                    bars = (
                        temp
                        .groupby(
                            "minute_key",
                            sort=False,
                        )["mid"]
                        .agg(
                            open="first",
                            high="max",
                            low="min",
                            close="last",
                        )
                        .reset_index()
                    )

                    all_chunk_bars.append(
                        bars
                    )

                    print(
                        f"  chunk {chunk_no:>3} "
                        f"rows processed: "
                        f"{total_rows:,}",
                        end="\r",
                    )

        print(
            f"\nCompleted {filename}: "
            f"{total_rows:,} tick rows"
        )

    if not all_chunk_bars:
        raise RuntimeError(
            "No M1 bars were created."
        )

    print("\nCombining chunk-level M1 bars...")

    m1 = pd.concat(
        all_chunk_bars,
        ignore_index=True,
    )

    # A minute can straddle two input chunks.
    # Re-aggregate once more in original row order.
    m1 = (
        m1
        .groupby(
            "minute_key",
            sort=False,
        )
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
        )
        .reset_index()
    )

    m1["timestamp"] = pd.to_datetime(
        m1["minute_key"],
        format="%Y.%m.%d %H:%M",
        utc=True,
        errors="coerce",
    )

    m1 = (
        m1
        .dropna(
            subset=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        )
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )

    m1 = m1[
        [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
        ]
    ]

    print(
        f"M1 rows created: {len(m1):,}"
    )

    print(
        f"Coverage: {m1['timestamp'].min()} "
        f"to {m1['timestamp'].max()}"
    )

    print(
        f"Saving reusable M1 cache:\n{M1_CACHE}"
    )

    m1.to_csv(
        M1_CACHE,
        index=False,
        compression="gzip",
    )

    return m1


def load_or_build_m1():
    if M1_CACHE.exists():
        print("\n============================================")
        print("LOADING EXISTING M1 CACHE")
        print("============================================")
        print(M1_CACHE)

        m1 = pd.read_csv(
            M1_CACHE,
            parse_dates=["timestamp"],
        )

        m1["timestamp"] = pd.to_datetime(
            m1["timestamp"],
            utc=True,
            errors="coerce",
        )

        return (
            m1
            .dropna()
            .sort_values("timestamp")
            .drop_duplicates("timestamp")
            .reset_index(drop=True)
        )

    return build_m1_midquote()


def sample_summary(frame, sample_name):
    g = frame.copy()

    real_resolved = g[
        "real_first_touch"
    ].isin(["UPPER", "LOWER"])

    mirror_resolved = g[
        "mirror_first_touch"
    ].isin(["UPPER", "LOWER"])

    real_only = g[
        real_resolved
    ].copy()

    paired = g[
        real_resolved
        &
        mirror_resolved
    ].copy()

    if real_only.empty:
        return {
            "sample": sample_name,
            "events": len(g),
            "real_resolved_events": 0,
            "paired_resolved_events": 0,
        }

    out = {
        "sample": sample_name,
        "events": len(g),

        "real_resolution_rate": (
            real_resolved.mean()
        ),

        "mirror_resolution_rate": (
            mirror_resolved.mean()
        ),

        "real_resolved_events": len(
            real_only
        ),

        "real_nearer_first": (
            real_only[
                "real_nearer_won"
            ].mean()
        ),

        "geometry_expected_nearer_first": (
            real_only[
                "p_nearer_geometry"
            ].mean()
        ),

        "real_residual_above_geometry": (
            (
                real_only[
                    "real_nearer_won"
                ]
                -
                real_only[
                    "p_nearer_geometry"
                ]
            ).mean()
        ),

        "paired_resolved_events": len(
            paired
        ),
    }

    if not paired.empty:
        out.update({
            "paired_real_nearer_first": (
                paired[
                    "real_nearer_won"
                ].mean()
            ),

            "paired_mirror_nearer_first": (
                paired[
                    "mirror_nearer_won"
                ].mean()
            ),

            "real_minus_mirror": (
                (
                    paired[
                        "real_nearer_won"
                    ]
                    -
                    paired[
                        "mirror_nearer_won"
                    ]
                ).mean()
            ),
        })
    else:
        out.update({
            "paired_real_nearer_first": np.nan,
            "paired_mirror_nearer_first": np.nan,
            "real_minus_mirror": np.nan,
        })

    return out


def month_block_bootstrap(
    frame,
    sample_name,
    reps,
    seed,
):
    paired = frame[
        frame["real_first_touch"].isin(
            ["UPPER", "LOWER"]
        )
        &
        frame["mirror_first_touch"].isin(
            ["UPPER", "LOWER"]
        )
    ].copy()

    if paired.empty:
        return {
            "sample": sample_name,
            "paired_resolved_events": 0,
            "month_blocks": 0,
        }

    paired["paired_advantage"] = (
        paired["real_nearer_won"]
        -
        paired["mirror_nearer_won"]
    )

    paired["month_block"] = (
        paired["decision_time"]
        .dt.strftime("%Y-%m")
    )

    blocks = (
        paired
        .groupby("month_block")[
            "paired_advantage"
        ]
        .agg(["sum", "count"])
        .reset_index(drop=True)
    )

    sums = blocks[
        "sum"
    ].to_numpy(dtype=float)

    counts = blocks[
        "count"
    ].to_numpy(dtype=float)

    n_blocks = len(blocks)

    rng = np.random.default_rng(
        seed
    )

    boot = np.empty(
        reps,
        dtype=float,
    )

    for i in range(reps):
        draw = rng.integers(
            0,
            n_blocks,
            size=n_blocks,
        )

        boot[i] = (
            sums[draw].sum()
            /
            counts[draw].sum()
        )

    low, high = np.quantile(
        boot,
        [0.025, 0.975],
    )

    observed = (
        paired[
            "paired_advantage"
        ].mean()
    )

    return {
        "sample": sample_name,
        "paired_resolved_events": len(
            paired
        ),
        "month_blocks": n_blocks,
        "observed_real_minus_mirror": (
            observed
        ),
        "bootstrap_mean": (
            boot.mean()
        ),
        "bootstrap_95pct_low": low,
        "bootstrap_95pct_high": high,
        "fraction_bootstrap_le_0": (
            boot <= 0
        ).mean(),
    }


# ============================================================
# LOAD / BUILD EURUSD M1 MIDQUOTE
# ============================================================

m1 = load_or_build_m1()

print("\n============================================")
print("EURUSD M1 MIDQUOTE DATA")
print("============================================")
print(f"Rows:  {len(m1):,}")
print(f"Start: {m1['timestamp'].min()}")
print(f"End:   {m1['timestamp'].max()}")


# ============================================================
# ASSIGN STANDARD FX SESSIONS
# ============================================================

# Convert UTC timestamp to New York time.
ny_time = (
    m1["timestamp"]
    .dt.tz_convert(
        "America/New_York"
    )
)

# Session closing date:
# Sunday 17:00 -> Monday 17:00 is labelled Monday.
session_anchor = (
    ny_time
    - pd.Timedelta(hours=17)
)

session_close_date = (
    session_anchor
    .dt.normalize()
    + pd.Timedelta(days=1)
)

# Keep as timezone-naive date label for grouping/output.
m1["session_date"] = (
    session_close_date
    .dt.date
)

session_summary = (
    m1
    .groupby(
        "session_date",
        sort=True,
    )
    .agg(
        session_start=(
            "timestamp",
            "min",
        ),
        session_end=(
            "timestamp",
            "max",
        ),
        minute_count=(
            "timestamp",
            "size",
        ),
        session_open=(
            "open",
            "first",
        ),
        session_high=(
            "high",
            "max",
        ),
        session_low=(
            "low",
            "min",
        ),
        session_close=(
            "close",
            "last",
        ),
    )
    .reset_index()
)

session_summary[
    "session_date"
] = pd.to_datetime(
    session_summary[
        "session_date"
    ]
)

safe_to_csv(
    session_summary,
    "EURUSD_OOS1_session_summary.csv",
)

print("\n============================================")
print("FX SESSION SUMMARY")
print("============================================")
print(
    f"Sessions observed: "
    f"{len(session_summary):,}"
)

print(
    f"Sessions >= {MIN_SESSION_MINUTES} M1: "
    f"{(session_summary['minute_count'] >= MIN_SESSION_MINUTES).sum():,}"
)


# ============================================================
# BUILD ONE PDH/PDL EVENT PER ELIGIBLE SESSION
# ============================================================

events = []

session_dates = (
    session_summary[
        "session_date"
    ].tolist()
)

# Pre-index M1 rows by session for path access.
session_groups = {
    pd.Timestamp(k): g.reset_index(drop=True)
    for k, g in m1.groupby(
        pd.to_datetime(
            m1["session_date"]
        ),
        sort=True,
    )
}

print("\n============================================")
print("BUILDING PDH/PDL EVENTS")
print("============================================")

for i in range(
    1,
    len(session_summary),
):
    prev = (
        session_summary
        .iloc[i - 1]
    )

    cur = (
        session_summary
        .iloc[i]
    )

    prev_date = pd.Timestamp(
        prev["session_date"]
    )

    cur_date = pd.Timestamp(
        cur["session_date"]
    )

    # Fixed data quality gate.
    if (
        int(prev["minute_count"])
        < MIN_SESSION_MINUTES
    ):
        continue

    if (
        int(cur["minute_count"])
        < MIN_SESSION_MINUTES
    ):
        continue

    current_path = session_groups.get(
        cur_date
    )

    if (
        current_path is None
        or current_path.empty
    ):
        continue

    pdh = float(
        prev["session_high"]
    )

    pdl = float(
        prev["session_low"]
    )

    decision_time = pd.Timestamp(
        current_path[
            "timestamp"
        ].iloc[0]
    )

    p0 = float(
        current_path[
            "open"
        ].iloc[0]
    )

    # Must begin inside prior session range.
    if not (
        pdl < p0 < pdh
    ):
        continue

    du = (
        pdh - p0
    )

    dl = (
        p0 - pdl
    )

    if (
        du <= 0
        or dl <= 0
    ):
        continue

    near_side = (
        "UPPER"
        if du < dl
        else "LOWER"
    )

    near_distance = min(
        du,
        dl,
    )

    far_distance = max(
        du,
        dl,
    )

    ratio = (
        near_distance
        /
        far_distance
    )

    p_nearer_geometry = (
        far_distance
        /
        (
            near_distance
            +
            far_distance
        )
    )

    # Real PDH/PDL.
    real_upper = pdh
    real_lower = pdl

    highs = current_path[
        "high"
    ].to_numpy(
        dtype=float
    )

    lows = current_path[
        "low"
    ].to_numpy(
        dtype=float
    )

    real_touch, real_rel = (
        first_touch_from_path(
            highs,
            lows,
            real_upper,
            real_lower,
        )
    )

    # Exact-distance mirrored placebo.
    mirror_upper = (
        p0 + dl
    )

    mirror_lower = (
        p0 - du
    )

    mirror_near_side = (
        "UPPER"
        if dl < du
        else "LOWER"
    )

    mirror_touch, mirror_rel = (
        first_touch_from_path(
            highs,
            lows,
            mirror_upper,
            mirror_lower,
        )
    )

    real_nearer_won = (
        int(
            real_touch
            == near_side
        )
        if real_touch
        in ["UPPER", "LOWER"]
        else np.nan
    )

    mirror_nearer_won = (
        int(
            mirror_touch
            == mirror_near_side
        )
        if mirror_touch
        in ["UPPER", "LOWER"]
        else np.nan
    )

    events.append({
        "session_date": cur_date,
        "year": int(
            cur_date.year
        ),

        "decision_time": decision_time,

        "previous_session_date": (
            prev_date
        ),

        "previous_session_minutes": int(
            prev["minute_count"]
        ),

        "current_session_minutes": int(
            cur["minute_count"]
        ),

        "entry_mid": p0,

        "PDH": pdh,
        "PDL": pdl,

        "upper_distance": du,
        "lower_distance": dl,

        "near_side": near_side,
        "near_distance": (
            near_distance
        ),
        "far_distance": (
            far_distance
        ),
        "near_far_ratio": ratio,

        "strong_ratio_le_0_50": (
            ratio
            <= STRONG_RATIO_MAX
        ),

        "p_nearer_geometry": (
            p_nearer_geometry
        ),

        "real_first_touch": (
            real_touch
        ),

        "real_nearer_won": (
            real_nearer_won
        ),

        "real_touch_minutes": (
            float(real_rel)
            if real_rel is not None
            else np.nan
        ),

        "mirror_upper": (
            mirror_upper
        ),
        "mirror_lower": (
            mirror_lower
        ),

        "mirror_near_side": (
            mirror_near_side
        ),

        "mirror_first_touch": (
            mirror_touch
        ),

        "mirror_nearer_won": (
            mirror_nearer_won
        ),

        "mirror_touch_minutes": (
            float(mirror_rel)
            if mirror_rel is not None
            else np.nan
        ),
    })


event_df = pd.DataFrame(
    events
)

if event_df.empty:
    raise RuntimeError(
        "No eligible EURUSD PDH/PDL events were generated."
    )

event_df[
    "decision_time"
] = pd.to_datetime(
    event_df[
        "decision_time"
    ],
    utc=True,
)

print(
    f"Eligible PDH/PDL events: "
    f"{len(event_df):,}"
)

print(
    f"Strong ratio <= {STRONG_RATIO_MAX}: "
    f"{event_df['strong_ratio_le_0_50'].sum():,}"
)


# ============================================================
# PRIMARY RESULTS
# ============================================================

sample_defs = [
    (
        "ALL_ELIGIBLE",
        event_df
    ),
    (
        "STRONG_RATIO_<=0.50",
        event_df[
            event_df[
                "strong_ratio_le_0_50"
            ]
        ]
    ),
]

summary_rows = []
bootstrap_rows = []

for name, g in sample_defs:
    summary_rows.append(
        sample_summary(
            g,
            name,
        )
    )

    bootstrap_rows.append(
        month_block_bootstrap(
            g,
            name,
            reps=BOOTSTRAP_REPS,
            seed=BOOTSTRAP_SEED,
        )
    )

primary_summary = pd.DataFrame(
    summary_rows
)

bootstrap_summary = pd.DataFrame(
    bootstrap_rows
)


# ============================================================
# YEAR-BY-YEAR
# ============================================================

year_rows = []

for year in sorted(
    event_df["year"].unique()
):
    yg = event_df[
        event_df[
            "year"
        ] == year
    ]

    for name, g in [
        (
            "ALL_ELIGIBLE",
            yg
        ),
        (
            "STRONG_RATIO_<=0.50",
            yg[
                yg[
                    "strong_ratio_le_0_50"
                ]
            ]
        ),
    ]:
        row = sample_summary(
            g,
            f"{year}_{name}",
        )

        row[
            "year"
        ] = int(
            year
        )

        row[
            "subset"
        ] = name

        year_rows.append(
            row
        )

year_summary = pd.DataFrame(
    year_rows
)


# ============================================================
# SIDE / DIRECTION DIAGNOSTIC
# ============================================================

side_rows = []

for side in [
    "UPPER",
    "LOWER",
]:
    g = event_df[
        event_df[
            "near_side"
        ] == side
    ]

    if g.empty:
        continue

    row = sample_summary(
        g,
        f"NEAR_{side}",
    )

    row[
        "near_side"
    ] = side

    side_rows.append(
        row
    )

side_summary = pd.DataFrame(
    side_rows
)


# ============================================================
# GEOMETRY CALIBRATION
# ============================================================

resolved_real = event_df[
    event_df[
        "real_first_touch"
    ].isin(
        ["UPPER", "LOWER"]
    )
].copy()

if len(resolved_real) >= 10:
    resolved_real[
        "geometry_bin"
    ] = pd.qcut(
        resolved_real[
            "p_nearer_geometry"
        ],
        q=min(
            10,
            len(
                resolved_real
            )
        ),
        duplicates="drop",
    )

    geometry_calibration = (
        resolved_real
        .groupby(
            "geometry_bin",
            observed=True,
        )
        .agg(
            observations=(
                "real_nearer_won",
                "size",
            ),
            observed_nearer_first=(
                "real_nearer_won",
                "mean",
            ),
            geometry_expected_nearer_first=(
                "p_nearer_geometry",
                "mean",
            ),
            mean_near_far_ratio=(
                "near_far_ratio",
                "mean",
            ),
        )
        .reset_index()
    )

    geometry_calibration[
        "residual"
    ] = (
        geometry_calibration[
            "observed_nearer_first"
        ]
        -
        geometry_calibration[
            "geometry_expected_nearer_first"
        ]
    )

    geometry_calibration[
        "geometry_bin"
    ] = (
        geometry_calibration[
            "geometry_bin"
        ].astype(str)
    )

else:
    geometry_calibration = pd.DataFrame()


# ============================================================
# SAVE
# ============================================================

safe_to_csv(
    event_df,
    "EURUSD_OOS1_event_level.csv",
)

safe_to_csv(
    primary_summary,
    "EURUSD_OOS1_primary_summary.csv",
)

safe_to_csv(
    bootstrap_summary,
    "EURUSD_OOS1_month_block_bootstrap.csv",
)

safe_to_csv(
    year_summary,
    "EURUSD_OOS1_year_summary.csv",
)

safe_to_csv(
    side_summary,
    "EURUSD_OOS1_side_summary.csv",
)

safe_to_csv(
    geometry_calibration,
    "EURUSD_OOS1_geometry_calibration.csv",
)


# ============================================================
# VERDICT FILE
# ============================================================

lines = [
    "EURUSD.OOS1 — PDH/PDL STRUCTURAL DESTINATION REPLICATION",
    "",
    "This is an external-market test of the hypothesis extracted",
    "from BTC. No EURUSD parameter tuning is performed.",
    "",
    "Previous day = previous 17:00 New York to 17:00 New York",
    "standard FX trading session.",
    "",
    "Price series = M1 midpoint quotes from raw BID/ASK ticks.",
    "",
]

for _, r in primary_summary.iterrows():
    lines.extend([
        f"SAMPLE: {r['sample']}",
        f"Events: {int(r['events'])}",
        f"Real resolved events: {int(r.get('real_resolved_events', 0))}",
        (
            f"Observed real nearer-first: "
            f"{r['real_nearer_first']:.6f}"
            if pd.notna(
                r.get(
                    "real_nearer_first",
                    np.nan,
                )
            )
            else "Observed real nearer-first: NA"
        ),
        (
            f"Geometry expectation: "
            f"{r['geometry_expected_nearer_first']:.6f}"
            if pd.notna(
                r.get(
                    "geometry_expected_nearer_first",
                    np.nan,
                )
            )
            else "Geometry expectation: NA"
        ),
        (
            f"Residual above geometry: "
            f"{r['real_residual_above_geometry']:.6f} "
            f"({r['real_residual_above_geometry']*100:.3f} pp)"
            if pd.notna(
                r.get(
                    "real_residual_above_geometry",
                    np.nan,
                )
            )
            else "Residual above geometry: NA"
        ),
        (
            f"Paired real nearer-first: "
            f"{r['paired_real_nearer_first']:.6f}"
            if pd.notna(
                r.get(
                    "paired_real_nearer_first",
                    np.nan,
                )
            )
            else "Paired real nearer-first: NA"
        ),
        (
            f"Paired mirror nearer-first: "
            f"{r['paired_mirror_nearer_first']:.6f}"
            if pd.notna(
                r.get(
                    "paired_mirror_nearer_first",
                    np.nan,
                )
            )
            else "Paired mirror nearer-first: NA"
        ),
        (
            f"Real - mirror: "
            f"{r['real_minus_mirror']:.6f} "
            f"({r['real_minus_mirror']*100:.3f} pp)"
            if pd.notna(
                r.get(
                    "real_minus_mirror",
                    np.nan,
                )
            )
            else "Real - mirror: NA"
        ),
        "",
    ])

lines.extend([
    "PREDECLARED DECISION LOGIC",
    "--------------------------",
    "",
    "The strongest BTC-derived claim is the STRONG_RATIO_<=0.50",
    "sample. EURUSD should not be tuned to rescue that hypothesis.",
    "",
    "Evidence supporting transfer would require:",
    "1) positive residual above geometry,",
    "2) positive real-minus-mirror advantage,",
    "3) reasonable year stability, and",
    "4) preferably a positive month-block bootstrap interval.",
    "",
    "If EURUSD fails, report the failure. Do not modify the ratio",
    "threshold or session definition after seeing these results.",
])

(
    OUTPUT_FOLDER /
    "EURUSD_OOS1_verdict.txt"
).write_text(
    "\n".join(lines),
    encoding="utf-8",
)


config_text = f"""EURUSD.OOS1 — FROZEN PDH/PDL DESTINATION REPLICATION

RAW FILES:
{FILES}

AVAILABLE SOURCE COVERAGE:
2023 file begins in August 2023.
2026 file ends in July 2026.

M1 CONSTRUCTION:
midpoint = (bid + ask) / 2
one-sided quote updates are causally forward-filled.

FX SESSION:
17:00 America/New_York to 17:00 America/New_York
DST handled by timezone conversion.

DATA QUALITY:
previous and current sessions each require >= {MIN_SESSION_MINUTES} M1 observations.

DECISION:
first available M1 open midpoint of current FX session.

ELIGIBILITY:
PDL < decision price < PDH

REAL BARRIERS:
upper = previous session high (PDH)
lower = previous session low (PDL)

GEOMETRY NULL:
P(nearer first) = far_distance / (near_distance + far_distance)

MIRROR PLACEBO:
upper = P0 + original lower distance
lower = P0 - original upper distance

PRIMARY BTC-FROZEN SUBSET:
near/far <= {STRONG_RATIO_MAX}

BOOTSTRAP:
calendar-month block bootstrap
repetitions = {BOOTSTRAP_REPS}
seed = {BOOTSTRAP_SEED}

IMPORTANT:
- This is an external-market structural/destination test.
- No GMMA, DC, stop, or P&L logic is included.
- No EURUSD parameter search is performed.
- Midquote is used to avoid bid/ask side bias in the structural test.
"""

(
    OUTPUT_FOLDER /
    "EURUSD_OOS1_config.txt"
).write_text(
    config_text,
    encoding="utf-8",
)


# ============================================================
# TERMINAL OUTPUT
# ============================================================

print("\n============================================")
print("EURUSD.OOS1 PRIMARY SUMMARY")
print("============================================")
print(
    primary_summary.to_string(
        index=False
    )
)

print("\n============================================")
print("EURUSD.OOS1 MONTH-BLOCK BOOTSTRAP")
print("============================================")
print(
    bootstrap_summary.to_string(
        index=False
    )
)

print("\n============================================")
print("YEAR-BY-YEAR")
print("============================================")
print(
    year_summary.to_string(
        index=False
    )
)

print("\n============================================")
print("FILES SAVED")
print("============================================")

for file in sorted(
    OUTPUT_FOLDER.iterdir()
):
    print(file)

print("\nDone.")
