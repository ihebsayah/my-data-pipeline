from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forecasting.features import PRIMARY_FEATURE_COLUMNS, build_primary_features
from forecasting.modeling import (
    build_model_frame,
    get_split_masks,
    load_frozen_bundle,
    make_hgb_pipeline,
)
from forecasting.reliability import (
    FeatureSchemaError,
    NonFiniteFeatureError,
    clip_nonnegative,
    deterministic_predictions,
    predict_feature_frame,
    prediction_health,
    validate_feature_schema,
    validate_feature_values,
)
from forecasting.robustness import (
    artifact_manifest,
    boundary_partial_impact,
    manifest_changed,
    mask_boundary_partial_history,
    segment_payload,
    volume_quartile_edges,
)

ARTIFACT_DIR = Path("data/processed/phase5b")


def _synthetic_frame(
    n: int = 600,
    boundary_rows: tuple[int, ...] = (),
    partial_rows: tuple[int, ...] = (),
) -> pd.DataFrame:
    hours = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    demand = 1_000.0 + np.arange(n, dtype=float) * 2.0
    status = np.full(n, "complete", dtype=object)
    for row in boundary_rows:
        status[row] = "boundary"
    for row in partial_rows:
        status[row] = "partial"
    return pd.DataFrame(
        {
            "hour_utc": hours,
            "rides_total": demand,
            "rides_member": demand * 0.7,
            "rides_casual": demand * 0.3,
            "coverage_status": status,
            "coverage_reason": "synthetic",
        }
    )


def _synthetic_model() -> tuple[object, pd.DataFrame]:
    rng = np.random.RandomState(7)
    features = pd.DataFrame(
        rng.normal(size=(240, len(PRIMARY_FEATURE_COLUMNS))),
        columns=list(PRIMARY_FEATURE_COLUMNS),
    )
    features.loc[::11, "lag_2"] = np.nan
    target = pd.Series(
        1_000.0 + features["lag_2"].fillna(0.0) * 2.0 + rng.normal(size=240)
    )
    model = make_hgb_pipeline()
    model.fit(features, target)
    return model, features


def test_feature_schema_requires_columns_and_ignores_extra_columns():
    model, features = _synthetic_model()
    shuffled = features.loc[:, list(reversed(PRIMARY_FEATURE_COLUMNS))].copy()
    shuffled["unexpected"] = 1

    selected = validate_feature_schema(shuffled)
    assert list(selected.columns) == list(PRIMARY_FEATURE_COLUMNS)

    with pytest.raises(FeatureSchemaError, match="lag_2"):
        validate_feature_schema(features.drop(columns=["lag_2"]))

    extra_prediction = predict_feature_frame(model, shuffled)
    base_prediction = predict_feature_frame(model, features)
    assert np.array_equal(extra_prediction, base_prediction)


def test_infinite_features_rejected_but_nan_is_allowed():
    _, features = _synthetic_model()
    validate_feature_values(features)

    infinite = features.copy()
    infinite.iloc[0, infinite.columns.get_loc("lag_2")] = np.inf
    with pytest.raises(NonFiniteFeatureError, match="infinite"):
        validate_feature_values(infinite)

    with pytest.raises(ValueError, match="non-numeric"):
        validate_feature_values(
            features.assign(lag_2="not-a-number")
        )


def test_inference_is_deterministic_and_finite_for_all_nan_input():
    model, features = _synthetic_model()
    deterministic = deterministic_predictions(model, features, repeats=3)
    assert deterministic["all_identical"] is True

    all_nan = pd.DataFrame(
        np.nan, index=range(3), columns=list(PRIMARY_FEATURE_COLUMNS)
    )
    predictions = predict_feature_frame(model, all_nan)
    assert np.isfinite(predictions).all()


def test_nonnegative_clipping_is_idempotent_and_health_is_reported():
    values = np.array([-3.0, 0.0, 5.0])
    clipped = clip_nonnegative(values)
    assert np.array_equal(clipped, np.array([0.0, 0.0, 5.0]))
    assert np.array_equal(clip_nonnegative(clipped), clipped)

    health = prediction_health(values)
    assert health["n_negative"] == 1
    assert health["n_nonfinite"] == 0
    assert health["min"] == -3.0


def test_boundary_and_partial_history_affects_lags_and_rolls():
    frame = build_primary_features(
        _synthetic_frame(boundary_rows=(300,), partial_rows=(500,))
    ).df
    impact = boundary_partial_impact(frame)
    affected = impact["affected_any_historical_feature"]

    assert impact["source_rows"]["boundary"] == 1
    assert impact["source_rows"]["partial"] == 1
    assert bool(affected.loc[302]) is True  # lag_2 consumes row 300
    assert bool(affected.loc[324]) is True  # lag_24 consumes row 300
    assert bool(affected.loc[325]) is True  # roll2_*_24 window reaches row 300
    assert bool(affected.loc[469]) is True  # roll2_*_168 window reaches row 300
    assert bool(affected.loc[301]) is False
    assert bool(affected.loc[502]) is True  # lag_2 consumes partial row 500


def test_masked_boundary_history_is_nan_not_zero_filled():
    source = _synthetic_frame(boundary_rows=(300,))
    frame = build_primary_features(source).df
    masked_source = mask_boundary_partial_history(frame)
    masked = build_primary_features(masked_source).df

    assert pd.isna(masked.loc[300, "rides_total"])
    assert pd.isna(masked.loc[302, "lag_2"])
    assert pd.isna(masked.loc[302, "roll2_mean_24"])
    assert pd.isna(masked.loc[302, "roll2_mean_168"])
    assert not pd.isna(masked.loc[301, "lag_2"])
    assert (masked.loc[masked["rides_total"].notna(), "rides_total"] > 0).all()


def test_segment_counts_separate_boundary_from_complete_coverage():
    source = _synthetic_frame(n=1000, boundary_rows=(720, 721, 722))
    frame = build_model_frame(source).df
    masks = get_split_masks(frame)
    edges = volume_quartile_edges(frame, masks["train_complete"])
    predictions = {
        "frozen_hgb": frame["rides_total"].fillna(0.0),
        "lag_24": frame["rides_total"].fillna(0.0),
    }
    affected = pd.Series(False, index=frame.index)
    payload = segment_payload(
        frame,
        predictions,
        masks["validation"],
        "validation",
        edges,
        affected,
    )
    assert payload["n_complete_coverage_targets"] <= payload["n_target_notna"]
    assert payload["n_complete_coverage_targets"] == int(
        (
            masks["validation"]
            & frame["coverage_status"].eq("complete")
            & frame["rides_total"].notna()
        ).sum()
    )
    assert payload["n_common_mask"] == payload["n_complete_coverage_targets"]


def test_artifact_manifest_detects_changes(tmp_path: Path):
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    before = artifact_manifest(tmp_path)
    (tmp_path / "a.txt").write_text("two", encoding="utf-8")
    (tmp_path / "b.txt").write_text("new", encoding="utf-8")
    after = artifact_manifest(tmp_path)
    diff = manifest_changed(before, after)
    assert diff["unchanged"] is False
    assert diff["changed"] == ["a.txt"]
    assert diff["added"] == ["b.txt"]


@pytest.mark.skipif(
    not (ARTIFACT_DIR / "final_model.joblib").exists(),
    reason="Frozen Phase 5B artifacts are not present",
)
def test_frozen_artifact_schema_and_reliability_smoke():
    config, bundle, _, _ = load_frozen_bundle(ARTIFACT_DIR)
    assert list(config["feature_columns"]) == list(PRIMARY_FEATURE_COLUMNS)

    sample = pd.DataFrame(
        np.nan, index=range(4), columns=list(PRIMARY_FEATURE_COLUMNS)
    )
    predictions = predict_feature_frame(bundle["model"], sample)
    assert np.isfinite(predictions).all()
    deterministic = deterministic_predictions(
        bundle["model"], sample, repeats=2
    )
    assert deterministic["all_identical"] is True
