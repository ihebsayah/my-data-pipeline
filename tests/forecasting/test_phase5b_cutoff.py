from __future__ import annotations

import numpy as np
import pandas as pd

from forecasting.features import (
    PRIMARY_FEATURE_COLUMNS,
    build_primary_features,
)


def _synthetic_continuous_frame(n: int = 400) -> pd.DataFrame:
    hours = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    demand = 1_000.0 + np.arange(n, dtype=float) * 3.0
    return pd.DataFrame(
        {
            "hour_utc": hours,
            "rides_total": demand,
            # Simulate the legacy Phase 5A column being present in the dataset.
            # The primary builder must not expose or select it.
            "lag_1": np.r_[np.nan, demand[:-1]],
        }
    )


def test_synthetic_t_minus_1_exists_but_is_unavailable_under_cutoff():
    """t-1 demand exists, but the primary model may only use data <= t-2.

    This is the explicit Phase 5B cutoff scenario requested in the preflight:

    * lag_1 is excluded/removed from the primary model.
    * every rolling feature is shifted by 2, so its newest observation is t-2.
    * mutating demand(t-1) leaves all conservative features at t unchanged.
    * lag_2 equals demand(t-2) on the continuous UTC hourly spine.
    """
    df = _synthetic_continuous_frame(n=400)
    result = build_primary_features(df)
    features = result.df
    t = 300

    # lag_1 is not available to the primary model at all.
    assert "lag_1" not in PRIMARY_FEATURE_COLUMNS
    assert "lag_1" not in result.feature_columns
    assert "lag_1" not in features.columns

    # lag_2 is exactly demand(t-2) on the continuous UTC spine.
    assert features.loc[t, "lag_2"] == df.loc[t - 2, "rides_total"]

    # Rolling windows are shifted by 2: the 24-hour window covers t-25..t-2,
    # so the newest observation is demand(t-2), not demand(t-1).
    expected_mean_24 = df.loc[t - 25 : t - 2, "rides_total"].mean()
    expected_min_24 = df.loc[t - 25 : t - 2, "rides_total"].min()
    expected_max_24 = df.loc[t - 25 : t - 2, "rides_total"].max()
    expected_std_24 = df.loc[t - 25 : t - 2, "rides_total"].std()
    assert np.isclose(features.loc[t, "roll2_mean_24"], expected_mean_24)
    assert np.isclose(features.loc[t, "roll2_min_24"], expected_min_24)
    assert np.isclose(features.loc[t, "roll2_max_24"], expected_max_24)
    assert np.isclose(features.loc[t, "roll2_std_24"], expected_std_24)

    expected_mean_168 = df.loc[t - 169 : t - 2, "rides_total"].mean()
    assert np.isclose(features.loc[t, "roll2_mean_168"], expected_mean_168)

    # Mutating demand(t-1) must not change any conservative feature at t.
    mutated = df.copy()
    mutated.loc[t - 1, "rides_total"] = 999_999.0
    mutated_features = build_primary_features(mutated).df
    for column in PRIMARY_FEATURE_COLUMNS:
        original = features.loc[t, column]
        changed = mutated_features.loc[t, column]
        if pd.isna(original) or pd.isna(changed):
            assert pd.isna(original) and pd.isna(changed), column
        else:
            assert np.isclose(original, changed), column

    # The mutation is visible at t+1 through lag_2, proving the test is live.
    assert mutated_features.loc[t + 1, "lag_2"] == 999_999.0
