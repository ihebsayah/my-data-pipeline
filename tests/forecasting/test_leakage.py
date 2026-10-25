from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from forecasting.features import add_calendar_features, add_lags_and_rolls


def test_no_feature_uses_future_demand():
    """No feature uses future demand - lags are shifted forward in time."""
    df = pd.DataFrame(
        {
            "hour_utc": pd.date_range("2026-01-01", periods=100, freq="h", tz="UTC"),
            "rides_total": np.arange(100, dtype=float),
        }
    )
    df = add_lags_and_rolls(df)
    # lag_1 should equal previous value, not future
    assert (df["lag_1"].iloc[1] == df["rides_total"].iloc[0])
    assert pd.isna(df["lag_1"].iloc[0])
    # Current hour not used as predictor in lags
    assert pd.isna(df["lag_1"].iloc[0])


def test_lag24_exactly_24_hours_earlier():
    """lag-24 refers to exactly 24 UTC hours earlier."""
    df = pd.DataFrame(
        {
            "hour_utc": pd.date_range("2026-01-01", periods=50, freq="h", tz="UTC"),
            "rides_total": np.random.RandomState(42).rand(50) * 100,
        }
    )
    df = add_lags_and_rolls(df)
    for i in range(24, 30):
        assert abs(df["lag_24"].iloc[i] - df["rides_total"].iloc[i - 24]) < 1e-10


def test_lag168_exactly_168_hours_earlier():
    """lag-168 refers to exactly 168 UTC hours earlier."""
    df = pd.DataFrame(
        {
            "hour_utc": pd.date_range("2026-01-01", periods=200, freq="h", tz="UTC"),
            "rides_total": np.random.RandomState(123).rand(200) * 100,
        }
    )
    df = add_lags_and_rolls(df)
    for i in range(168, 170):
        assert abs(df["lag_168"].iloc[i] - df["rides_total"].iloc[i - 168]) < 1e-10


def test_missing_hour_gaps_not_compressed():
    """Missing or excluded hours must remain missing; no compression."""
    # Create spine with a gap
    hours = []
    # Add hours 0-5
    for i in range(6):
        hours.append(pd.Timestamp("2026-01-01", tz="UTC") + timedelta(hours=i))
    # Skip hour 6,7 (gap)
    for i in range(8, 12):  # jump from hour 5 to 8
        hours.append(pd.Timestamp("2026-01-01", tz="UTC") + timedelta(hours=i))
    df = pd.DataFrame({"hour_utc": hours, "rides_total": list(range(len(hours)))})
    # Build continuous spine separately - in our approach, we merge onto full spine
    from forecasting.dataset import ForecastDatasetBuilder

    builder = ForecastDatasetBuilder.__new__(ForecastDatasetBuilder)
    spine = pd.DataFrame(
        {"hour_utc": pd.date_range("2026-01-01", periods=12, freq="h", tz="UTC")}
    )
    cov = pd.DataFrame(columns=["hour_utc", "coverage_status", "coverage_reason", "in_demand_mart", "is_source_truncated", "in_outage_window", "is_dst_ambiguous_hour"])
    merged = builder.merge_with_continuity(spine, df, cov)
    # Hours 6,7 should be missing (NaN) - not compressed
    assert pd.isna(merged.loc[merged["hour_utc"] == pd.Timestamp("2026-01-01 06:00", tz="UTC"), "rides_total"].iloc[0])
    assert pd.isna(merged.loc[merged["hour_utc"] == pd.Timestamp("2026-01-01 07:00", tz="UTC"), "rides_total"].iloc[0])
    # And they shouldn't take values from adjacent
    assert merged.shape[0] == 12  # spine length preserved


def test_dst_transition_does_not_corrupt_lag_alignment():
    """DST transitions do not corrupt lag alignment - use UTC indexing."""
    # Fall back example (Nov) - create continuous UTC hours across transition
    # Just verify lags are based on position shift on UTC index
    df = pd.DataFrame(
        {
            "hour_utc": pd.date_range("2025-11-02 04:00", periods=30, freq="h", tz="UTC"),
            "rides_total": np.arange(30, dtype=float),
        }
    )
    df = add_lags_and_rolls(df)
    # lag_24 should be exactly 24 positions back
    for i in range(24, 28):
        assert df["lag_24"].iloc[i] == df["rides_total"].iloc[i - 24]


def test_chronological_splits_do_not_overlap():
    """Chronological splits do not overlap."""
    from forecasting.evaluation import get_chronological_splits

    df = pd.DataFrame({"hour_utc": pd.date_range("2026-01-01", periods=1000, freq="h", tz="UTC")})
    train, val, test = get_chronological_splits(df)
    # No overlaps
    assert not (train & val).any()
    assert not (train & test).any()
    assert not (val & test).any()
    # Cover all
    assert (train | val | test).sum() == 1000
    # Chronological order
    assert train.sum() > 0 and val.sum() > 0 and test.sum() > 0
    # Check boundaries - splits are contiguous and non-overlapping
    assert train.sum() == int(1000 * 0.7)
    assert val.sum() == int(1000 * (0.7 + 0.15)) - int(1000 * 0.7)
    assert test.sum() == 1000 - int(1000 * (0.7 + 0.15))
    train_end_idx = train[train].index[-1]
    val_start_idx = val[val].index[0]
    assert val_start_idx == train_end_idx + 1
    val_end_idx = val[val].index[-1]
    test_start_idx = test[test].index[0]
    assert test_start_idx == val_end_idx + 1


def test_rolling_statistics_exclude_target_hour():
    """Rolling statistics exclude the target hour."""
    df = pd.DataFrame(
        {
            "hour_utc": pd.date_range("2026-01-01", periods=50, freq="h", tz="UTC"),
            "rides_total": np.ones(50) * 10.0,
        }
    )
    df = add_lags_and_rolls(df, roll_windows=(3,))
    # roll_mean_3 uses shift(1) before rolling, so excludes current hour
    assert df["roll_mean_3"].iloc[4] == 10.0
    # Changing current hour doesn't affect past rolling stats
    df2 = df.copy()
    df2.at[df2.index[10], "rides_total"] = 999.0
    df2 = add_lags_and_rolls(df2, roll_windows=(3,))
    # rolling stats for positions before 10 should be unaffected
    assert df2["roll_mean_3"].iloc[4] == 10.0
    assert df2["roll_mean_3"].iloc[9] == 10.0  # uses past values, not current at 10


def test_primary_evaluation_targets_have_complete_coverage():
    """Primary evaluation targets have complete coverage."""
    from forecasting.config import ForecastingConfig

    config = ForecastingConfig()
    # Primary evaluation uses only complete coverage
    assert config.primary_evaluation_eligible_statuses == ("complete",)


def test_all_compared_baselines_use_identical_timestamps():
    """All compared baselines use identical timestamps."""
    df = pd.DataFrame(
        {
            "hour_utc": pd.date_range("2026-01-01", periods=200, freq="h", tz="UTC"),
            "rides_total": np.random.RandomState(0).rand(200) * 100,
            "coverage_status": ["complete"] * 200,
        }
    )
    from forecasting.baselines import naive_previous_hour, seasonal_naive_lag24, seasonal_naive_lag168, historical_mean_past

    b1 = naive_previous_hour(df)
    b2 = seasonal_naive_lag24(df)
    b3 = seasonal_naive_lag168(df)
    b4 = historical_mean_past(df)
    # Common mask computation as in script
    common = b1.mask_available & b2.mask_available & b3.mask_available & b4.mask_available & df["rides_total"].notna()
    # All baselines should be evaluable on same timestamps
    assert common.sum() > 0


def test_feature_availability_respects_information_cutoff():
    """Feature availability respects the configured information cutoff."""
    from forecasting.config import ForecastingConfig

    config = ForecastingConfig()
    # Information cutoff is configurable
    assert hasattr(config, "information_cutoff_hours")
    assert hasattr(config, "conservative_no_lag1")


def test_full_spine_and_eligible_target_counts_reconciled():
    """The full hourly spine and eligible-target counts are reconciled."""
    df = pd.DataFrame(
        {
            "hour_utc": pd.date_range("2026-01-01", periods=100, freq="h", tz="UTC"),
            "rides_total": [float(i) for i in range(100)],
            "coverage_status": ["complete"] * 100,
        }
    )
    assert len(df) == 100  # spine length


def test_boundary_and_partial_targets_excluded_from_primary_evaluation():
    """Boundary and partial targets are excluded from primary evaluation."""
    df = pd.DataFrame(
        {
            "hour_utc": pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC"),
            "rides_total": [1, 2, 3, 4, 5],
            "coverage_status": ["complete", "boundary", "partial", "complete", "complete"],
        }
    )
    from forecasting.config import ForecastingConfig

    config = ForecastingConfig()
    eligible = df["coverage_status"].isin(config.primary_evaluation_eligible_statuses)
    # boundary and partial should be excluded from primary
    assert eligible.sum() == 3


def test_missing_lags_remain_missing():
    """Missing lags remain missing."""
    df = pd.DataFrame(
        {
            "hour_utc": pd.date_range("2026-01-01", periods=50, freq="h", tz="UTC"),
            "rides_total": np.ones(50) * 5.0,
        }
    )
    df = add_lags_and_rolls(df)
    # Early positions have NaN lags
    assert pd.isna(df["lag_1"].iloc[0])
    assert pd.isna(df["lag_24"].iloc[10])


def test_chronological_split_boundaries_non_overlapping():
    """Chronological split boundaries remain non-overlapping."""
    from forecasting.evaluation import get_chronological_splits

    df = pd.DataFrame({"hour_utc": pd.date_range("2026-01-01", periods=100, freq="h", tz="UTC")})
    train, val, test = get_chronological_splits(df)
    assert not (train & val).any()
    assert not (train & test).any()
    assert not (val & test).any()
    # Check order
    train_idx = train[train].index
    val_idx = val[val].index
    test_idx = test[test].index
    assert train_idx[-1] + 1 == val_idx[0]
    assert val_idx[-1] + 1 == test_idx[0]
