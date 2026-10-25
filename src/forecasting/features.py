from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd
import pytz


LOCAL_TZ = pytz.timezone("America/New_York")

# Phase 5B primary model contract: the newest demand observation permitted at
# prediction time is t-2.  lag_1 is therefore never computed or selected.
PRIMARY_CUTOFF_HOURS = 2
PRIMARY_LAG_HOURS: tuple[int, ...] = (2, 24, 48, 72, 168)
PRIMARY_ROLL_WINDOWS: tuple[int, ...] = (24, 168)

CALENDAR_FEATURE_COLUMNS: tuple[str, ...] = (
    "hour_local",
    "day_of_week_local",
    "is_weekend_local",
    "month",
    "day_of_month_local",
    "is_month_start",
    "is_month_end",
    "isodow_local",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "month_sin",
    "month_cos",
)

PRIMARY_HISTORICAL_FEATURE_COLUMNS: tuple[str, ...] = (
    "lag_2",
    "lag_24",
    "lag_48",
    "lag_72",
    "lag_168",
    "roll2_mean_24",
    "roll2_std_24",
    "roll2_min_24",
    "roll2_max_24",
    "roll2_mean_168",
    "roll2_std_168",
    "roll2_min_168",
    "roll2_max_168",
)

PRIMARY_FEATURE_COLUMNS: tuple[str, ...] = (
    CALENDAR_FEATURE_COLUMNS + PRIMARY_HISTORICAL_FEATURE_COLUMNS
)

# Columns that are never model predictors.  This includes the target, its
# components, coverage/audit flags, identifiers, and the unsafe Phase 5A
# lag_1/roll_* history (computed with shift(1)).
PROHIBITED_PRIMARY_FEATURES: tuple[str, ...] = (
    "rides_total",
    "rides_member",
    "rides_casual",
    "coverage_status",
    "coverage_reason",
    "in_demand_mart",
    "is_source_truncated_x",
    "is_source_truncated_y",
    "is_dst_ambiguous_hour",
    "in_outage_window",
    "hour_utc",
    "hour_local_start",
    "hour_local_date",
    "lag_1",
    "roll_mean_24",
    "roll_std_24",
    "roll_min_24",
    "roll_max_24",
    "roll_mean_168",
    "roll_std_168",
    "roll_min_168",
    "roll_max_168",
)


@dataclass
class FeatureBuildResult:
    df: pd.DataFrame
    feature_columns: list[str]
    metadata: dict


def build_continuous_utc_spine(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Build continuous UTC hourly spine from start to end (inclusive of hours)."""
    # Generate hourly range
    hours = pd.date_range(start=start, end=end, freq="h", tz="UTC")
    df = pd.DataFrame({"hour_utc": hours})
    return df


def add_calendar_features(df: pd.DataFrame, local_tz=LOCAL_TZ) -> pd.DataFrame:
    df = df.copy()
    # Convert to local time
    local_times = df["hour_utc"].dt.tz_convert(local_tz)
    df["hour_local"] = local_times.dt.hour
    df["day_of_week_local"] = local_times.dt.dayofweek  # Monday=0 in pandas
    df["is_weekend_local"] = df["day_of_week_local"].isin([5, 6]).astype(int)
    df["month"] = local_times.dt.month
    df["day_of_month_local"] = local_times.dt.day
    df["is_month_start"] = local_times.dt.is_month_start.astype(int)
    df["is_month_end"] = local_times.dt.is_month_end.astype(int)
    # ISO day of week (Mon=1..Sun=7) for compatibility
    df["isodow_local"] = local_times.dt.dayofweek + 1

    # Cyclical encodings
    df["hour_sin"] = np.sin(2 * np.pi * df["hour_local"] / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour_local"] / 24.0)
    df["dow_sin"] = np.sin(2 * np.pi * df["day_of_week_local"] / 7.0)
    df["dow_cos"] = np.cos(2 * np.pi * df["day_of_week_local"] / 7.0)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12.0)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12.0)
    return df


def add_lags_and_rolls(
    df: pd.DataFrame,
    target_col: str = "rides_total",
    lags: tuple[int, ...] = (1, 24, 48, 72, 168),
    roll_windows: tuple[int, ...] = (24, 168),
) -> pd.DataFrame:
    df = df.copy()
    df = df.sort_values("hour_utc").reset_index(drop=True)

    # Ensure continuous index alignment - work on hour_utc
    # Create a full range index for alignment
    # Lags against continuous UTC hourly spine
    for lag in lags:
        df[f"lag_{lag}"] = df[target_col].shift(lag)

    # Rolling statistics using only past observations (shift before rolling)
    for window in roll_windows:
        # shift(1) to exclude current hour
        df[f"roll_mean_{window}"] = df[target_col].shift(1).rolling(window=window, min_periods=window).mean()
        df[f"roll_std_{window}"] = df[target_col].shift(1).rolling(window=window, min_periods=window).std()
        df[f"roll_min_{window}"] = df[target_col].shift(1).rolling(window=window, min_periods=window).min()
        df[f"roll_max_{window}"] = df[target_col].shift(1).rolling(window=window, min_periods=window).max()

    return df


def build_features(
    demand_df: pd.DataFrame,
    coverage_df: Optional[pd.DataFrame] = None,
    target_col: str = "rides_total",
    local_tz=LOCAL_TZ,
    add_rolls: bool = True,
) -> FeatureBuildResult:
    """
    Build leakage-safe features on continuous UTC hourly spine.
    Missing/excluded hours remain missing - no forward/backward fill.
    """
    df = demand_df.copy()

    # Ensure hour_utc is datetime
    if not pd.api.types.is_datetime64_any_dtype(df["hour_utc"]):
        df["hour_utc"] = pd.to_datetime(df["hour_utc"], utc=True)
    else:
        if df["hour_utc"].dt.tz is None:
            df["hour_utc"] = df["hour_utc"].dt.tz_localize("UTC")
        else:
            df["hour_utc"] = df["hour_utc"].dt.tz_convert("UTC")

    # Add calendar features
    df = add_calendar_features(df, local_tz=local_tz)

    # Add lags and rolls (against continuous spine as-is)
    if add_rolls:
        df = add_lags_and_rolls(df, target_col=target_col)
    else:
        df = df.sort_values("hour_utc").reset_index(drop=True)
        for lag in (1, 24, 168):
            df[f"lag_{lag}"] = df[target_col].shift(lag)

    feature_columns = [
        "hour_local",
        "day_of_week_local",
        "is_weekend_local",
        "month",
        "hour_sin",
        "hour_cos",
        "dow_sin",
        "dow_cos",
        "month_sin",
        "month_cos",
        "lag_1",
        "lag_24",
        "lag_48",
        "lag_72",
        "lag_168",
    ]
    if add_rolls:
        feature_columns.extend(
            [
                "roll_mean_24",
                "roll_std_24",
                "roll_min_24",
                "roll_max_24",
                "roll_mean_168",
                "roll_std_168",
                "roll_min_168",
                "roll_max_168",
            ]
        )

    metadata = {
        "target_col": target_col,
        "local_tz": str(local_tz),
        "lags": [1, 24, 48, 72, 168],
        "roll_windows": [24, 168] if add_rolls else [],
        "note": "Lags computed on continuous UTC hourly spine via shift(); missing hours remain NaN (no fill).",
    }

    return FeatureBuildResult(
        df=df, feature_columns=feature_columns, metadata=metadata
    )


def validate_continuous_hourly_spine(
    df: pd.DataFrame, time_col: str = "hour_utc"
) -> None:
    """Raise if ``time_col`` is not a unique, sorted, continuous UTC hourly spine.

    The Phase 5B contract requires position-based lags/rolling statistics to
    correspond to exact UTC hour offsets.  Compressing or dropping rows would
    silently break that alignment, so the primary feature builder rejects such
    frames rather than guessing.
    """
    if time_col not in df.columns:
        raise ValueError(f"Missing time column: {time_col}")
    ts = df[time_col]
    if ts.isna().any():
        raise ValueError("hour_utc contains missing values")
    if ts.duplicated().any():
        raise ValueError("hour_utc contains duplicate hours")
    if not ts.is_monotonic_increasing:
        raise ValueError("hour_utc must be sorted ascending")
    if len(ts) > 1:
        diffs = ts.diff().dropna()
        expected = pd.Timedelta(hours=1)
        if not (diffs == expected).all():
            raise ValueError(
                "hour_utc must be a continuous hourly spine; "
                "missing hours must remain as rows, not be compressed"
            )


def assert_primary_feature_contract(feature_columns: list[str] | tuple[str, ...]) -> None:
    """Validate that a feature list respects the Phase 5B cutoff contract."""
    prohibited = set(feature_columns) & set(PROHIBITED_PRIMARY_FEATURES)
    if prohibited:
        raise ValueError(f"Prohibited primary feature columns: {sorted(prohibited)}")
    unknown = set(feature_columns) - set(PRIMARY_FEATURE_COLUMNS)
    if unknown:
        raise ValueError(f"Unknown primary feature columns: {sorted(unknown)}")
    if "lag_1" in feature_columns:
        raise ValueError("lag_1 is prohibited in the primary model (cutoff is t-2)")


def build_primary_features(
    demand_df: pd.DataFrame,
    target_col: str = "rides_total",
    local_tz=LOCAL_TZ,
    roll_min_periods: Optional[dict[int, int]] = None,
) -> FeatureBuildResult:
    """Build the Phase 5B conservative primary feature set.

    The newest historical demand observation used by any feature is ``t-2``:

    * ``lag_2`` is exactly ``demand(t-2)``.
    * ``lag_24/48/72/168`` are exact UTC-hour lags.
    * ``roll2_*_{24,168}`` are computed from ``demand.shift(2).rolling(...)``,
      so the rolling window ends at ``t-2``.
    * ``lag_1`` is deliberately not computed or selected.

    Calendar features are deterministic functions of the target timestamp and
    are available at prediction time.  Existing Phase 5A ``roll_*`` columns are
    not selected because they were built with ``shift(1)``.

    Missing hours must already be present as rows on a continuous UTC hourly
    spine.  They remain NaN; no fill/interpolation/compression is performed.
    Rolling statistics use full windows by default (``min_periods=window``).
    """
    if target_col not in demand_df.columns:
        raise ValueError(f"Missing target column: {target_col}")

    df = demand_df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["hour_utc"]):
        df["hour_utc"] = pd.to_datetime(df["hour_utc"], utc=True)
    elif df["hour_utc"].dt.tz is None:
        df["hour_utc"] = df["hour_utc"].dt.tz_localize("UTC")
    else:
        df["hour_utc"] = df["hour_utc"].dt.tz_convert("UTC")

    df = df.sort_values("hour_utc").reset_index(drop=True)
    validate_continuous_hourly_spine(df)

    # Make the unsafe Phase 5A history physically unavailable to the primary
    # model.  lag_1 violates the t-2 cutoff; the stored roll_* columns were
    # computed with shift(1).  They are dropped rather than silently reused.
    unsafe_history_columns = ["lag_1"]
    for window in (24, 168):
        for stat in ("mean", "std", "min", "max"):
            unsafe_history_columns.append(f"roll_{stat}_{window}")
    df = df.drop(
        columns=[c for c in unsafe_history_columns if c in df.columns]
    )

    df = add_calendar_features(df, local_tz=local_tz)

    # Exact UTC position lags.  lag_1 is intentionally absent.
    for lag in PRIMARY_LAG_HOURS:
        df[f"lag_{lag}"] = df[target_col].shift(lag)

    # Rolling statistics whose newest observation is t-2.
    shifted = df[target_col].shift(PRIMARY_CUTOFF_HOURS)
    configured_min_periods = roll_min_periods or {}
    for window in PRIMARY_ROLL_WINDOWS:
        min_periods = int(configured_min_periods.get(window, window))
        rolling = shifted.rolling(window=window, min_periods=min_periods)
        df[f"roll2_mean_{window}"] = rolling.mean()
        df[f"roll2_std_{window}"] = rolling.std()
        df[f"roll2_min_{window}"] = rolling.min()
        df[f"roll2_max_{window}"] = rolling.max()

    feature_columns = list(PRIMARY_FEATURE_COLUMNS)
    assert_primary_feature_contract(feature_columns)
    missing = set(feature_columns) - set(df.columns)
    if missing:
        raise ValueError(f"Primary feature build failed to create: {sorted(missing)}")

    metadata = {
        "target_col": target_col,
        "local_tz": str(local_tz),
        "information_cutoff_hours": PRIMARY_CUTOFF_HOURS,
        "information_cutoff_note": (
            "Latest permitted observed demand is t-2; lag_1 is excluded. "
            "Rolling statistics use demand.shift(2) so the newest observation "
            "in every window is t-2."
        ),
        "lags": list(PRIMARY_LAG_HOURS),
        "roll_windows": list(PRIMARY_ROLL_WINDOWS),
        "roll_min_periods": {
            str(w): int((roll_min_periods or {}).get(w, w))
            for w in PRIMARY_ROLL_WINDOWS
        },
        "lag_1_selected": False,
        "stored_phase5a_roll_columns_selected": False,
        "missing_hour_policy": (
            "Missing hours remain rows with NaN target/history; no fill, "
            "interpolation, backward/forward fill, or row compression."
        ),
    }

    return FeatureBuildResult(
        df=df, feature_columns=feature_columns, metadata=metadata
    )
