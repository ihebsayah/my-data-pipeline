from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting.features import (
    PRIMARY_FEATURE_COLUMNS,
    assert_primary_feature_contract,
    build_primary_features,
)
from forecasting.modeling import (
    build_common_mask,
    build_model_frame,
    evaluate_common_mask,
    fit_candidate,
    get_split_masks,
    improvement_vs_baseline,
    load_frozen_bundle,
    make_ridge_pipeline,
    predict_on_mask,
)

PROHIBITED_PREDICTORS = [
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
]


def _synthetic_frame(n: int = 400, coverage_status: str = "complete") -> pd.DataFrame:
    hours = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    demand = 1_000.0 + np.arange(n, dtype=float) * 3.0
    return pd.DataFrame(
        {
            "hour_utc": hours,
            "rides_total": demand,
            "rides_member": demand * 0.7,
            "rides_casual": demand * 0.3,
            "coverage_status": coverage_status,
            "coverage_reason": "synthetic",
            "in_demand_mart": True,
            "is_source_truncated_x": False,
            "is_source_truncated_y": False,
            "is_dst_ambiguous_hour": False,
            "in_outage_window": False,
            "hour_local_start": hours.astype(str),
            "hour_local_date": hours.date.astype(str),
        }
    )


def test_primary_feature_list_excludes_lag1_and_prohibited_fields():
    frame = _synthetic_frame(400)
    result = build_primary_features(frame)

    assert "lag_1" not in PRIMARY_FEATURE_COLUMNS
    assert "lag_1" not in result.feature_columns
    assert "lag_1" not in result.df.columns
    for column in PROHIBITED_PREDICTORS:
        assert column not in PRIMARY_FEATURE_COLUMNS
    assert set(PRIMARY_FEATURE_COLUMNS).issubset(result.df.columns)

    with pytest.raises(ValueError, match="lag_1"):
        assert_primary_feature_contract(["lag_1"])
    with pytest.raises(ValueError, match="Prohibited"):
        assert_primary_feature_contract(["rides_total"])


def test_prediction_time_features_ignore_target_and_future_values():
    frame = _synthetic_frame(400)
    baseline = build_primary_features(frame).df
    t = 300

    mutated = frame.copy()
    mutated.loc[t:, "rides_total"] = 999_999.0
    mutated.loc[t, "rides_member"] = 888_888.0
    mutated.loc[t, "rides_casual"] = 777_777.0
    mutated.loc[t, "coverage_status"] = "boundary"
    mutated.loc[t, "coverage_reason"] = "mutated_after_prediction"
    mutated_features = build_primary_features(mutated).df

    for column in PRIMARY_FEATURE_COLUMNS:
        original = baseline.loc[t, column]
        changed = mutated_features.loc[t, column]
        if pd.isna(original) or pd.isna(changed):
            assert pd.isna(original) and pd.isna(changed), column
        else:
            assert np.isclose(original, changed), column


def test_rolling_features_depend_only_on_data_through_t_minus_2():
    frame = _synthetic_frame(400)
    features = build_primary_features(frame).df
    t = 300

    expected_mean_24 = frame.loc[t - 25 : t - 2, "rides_total"].mean()
    expected_std_24 = frame.loc[t - 25 : t - 2, "rides_total"].std()
    expected_mean_168 = frame.loc[t - 169 : t - 2, "rides_total"].mean()

    assert np.isclose(features.loc[t, "roll2_mean_24"], expected_mean_24)
    assert np.isclose(features.loc[t, "roll2_std_24"], expected_std_24)
    assert np.isclose(features.loc[t, "roll2_mean_168"], expected_mean_168)
    assert features.loc[t, "lag_2"] == frame.loc[t - 2, "rides_total"]


def test_ridge_imputer_and_scaler_are_fit_on_training_data_only():
    X_train = pd.DataFrame(
        {
            "a": [1.0, 2.0, np.nan, 4.0, 5.0],
            "b": [10.0, np.nan, 30.0, 40.0, 50.0],
        }
    )
    y_train = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    X_validation = pd.DataFrame({"a": [100.0, np.nan], "b": [np.nan, 200.0]})

    pipeline = make_ridge_pipeline()
    pipeline.fit(X_train, y_train)

    imputer = pipeline.named_steps["imputer"]
    scaler = pipeline.named_steps["scaler"]
    expected_train_medians = np.nanmedian(X_train.to_numpy(), axis=0)
    assert np.allclose(imputer.statistics_, expected_train_medians)

    imputed_train = imputer.transform(X_train)
    assert np.allclose(scaler.mean_, imputed_train.mean(axis=0))

    combined = pd.concat([X_train, X_validation], ignore_index=True)
    expected_combined_medians = np.nanmedian(combined.to_numpy(), axis=0)
    assert not np.allclose(imputer.statistics_, expected_combined_medians)


def test_chronological_split_isolation():
    frame = build_model_frame(_synthetic_frame(1000)).df
    masks = get_split_masks(frame)

    assert masks["train"].sum() == 700
    assert masks["validation"].sum() == 150
    assert masks["test"].sum() == 150
    assert not (masks["train"] & masks["validation"]).any()
    assert not (masks["train"] & masks["test"]).any()
    assert not (masks["validation"] & masks["test"]).any()

    train_hours = frame.loc[masks["train_complete"], "hour_utc"]
    validation_hours = frame.loc[masks["validation_complete"], "hour_utc"]
    test_hours = frame.loc[masks["test_complete"], "hour_utc"]
    assert train_hours.max() < validation_hours.min()
    assert validation_hours.max() < test_hours.min()


def test_complete_only_targets_are_used_for_training_masks():
    frame = _synthetic_frame(100)
    frame.loc[0:9, "coverage_status"] = "boundary"
    frame.loc[10:19, "coverage_status"] = "unavailable"
    frame.loc[20:29, "coverage_status"] = "partial"
    frame.loc[30, "coverage_status"] = "complete"
    frame.loc[30, "rides_total"] = np.nan

    masks = get_split_masks(frame)
    assert not masks["train_complete"].loc[0:9].any()
    assert not masks["train_complete"].loc[10:29].any()
    assert not masks["train_complete"].loc[30]
    assert masks["train_complete"].loc[31:69].all()


def test_common_mask_comparison_uses_identical_timestamps_and_sign_convention():
    frame = _synthetic_frame(50)
    target = frame["rides_total"]
    model_prediction = pd.Series(target.to_numpy() - 1.0, index=frame.index)
    baseline_prediction = pd.Series(target.to_numpy() - 10.0, index=frame.index)
    model_prediction.iloc[10] = np.nan
    baseline_prediction.iloc[0:5] = np.nan

    split_mask = pd.Series(True, index=frame.index)
    common_mask, evaluated = evaluate_common_mask(
        frame,
        {"model": model_prediction, "lag24": baseline_prediction},
        split_mask,
        n_eligible_targets=50,
    )

    assert common_mask.sum() == 44
    assert not common_mask.iloc[0:5].any()
    assert not common_mask.iloc[10]
    assert evaluated["model"]["n_evaluated"] == 44
    assert evaluated["lag24"]["n_evaluated"] == 44
    assert np.isclose(evaluated["model"]["mae"], 1.0)
    assert np.isclose(evaluated["lag24"]["mae"], 10.0)

    improvement = improvement_vs_baseline(evaluated["model"], evaluated["lag24"])
    assert np.isclose(improvement["mae_absolute"], 9.0)
    assert np.isclose(improvement["mae_percent"], 90.0)

    with pytest.raises(ValueError, match="lag-1"):
        build_common_mask(
            frame,
            split_mask,
            {"lag_1_optimistic": model_prediction},
        )


def test_missing_hour_remains_missing_and_alignment_is_not_compressed():
    frame = _synthetic_frame(200)
    frame.loc[50, "rides_total"] = np.nan
    result = build_primary_features(frame)
    features = result.df

    assert len(features) == 200
    assert pd.isna(features.loc[50, "rides_total"])
    assert pd.isna(features.loc[52, "lag_2"])
    assert features.loc[51, "lag_2"] == frame.loc[49, "rides_total"]
    assert pd.isna(features.loc[52, "roll2_mean_24"])

    with pytest.raises(ValueError, match="continuous hourly spine"):
        build_primary_features(frame.drop(index=10).reset_index(drop=True))


def test_deterministic_predictions_for_both_candidates():
    frame = build_model_frame(_synthetic_frame(260)).df
    masks = get_split_masks(frame)
    train_mask = masks["train_complete"]
    prediction_mask = masks["validation"]

    ridge_params = {"alpha": 1.0}
    hgb_params = {
        "learning_rate": 0.1,
        "max_iter": 50,
        "max_leaf_nodes": 15,
    }

    for model_type, params in (
        ("ridge", ridge_params),
        ("hist_gradient_boosting", hgb_params),
    ):
        first = fit_candidate(model_type, params, frame, train_mask)
        second = fit_candidate(model_type, params, frame, train_mask)
        first_prediction = predict_on_mask(first, frame, prediction_mask)
        second_prediction = predict_on_mask(second, frame, prediction_mask)
        np.testing.assert_allclose(
            first_prediction.to_numpy(),
            second_prediction.to_numpy(),
            rtol=0,
            atol=0,
        )


def test_final_test_requires_frozen_configuration(tmp_path):
    with pytest.raises(FileNotFoundError, match="Frozen configuration"):
        load_frozen_bundle(tmp_path)
