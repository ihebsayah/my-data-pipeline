from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd

from forecasting.baselines import seasonal_naive_lag24
from forecasting.features import PRIMARY_FEATURE_COLUMNS, build_primary_features
from forecasting.modeling import (
    DEFAULT_ARTIFACT_DIR,
    TARGET_COL,
    build_common_mask,
    build_model_frame,
    dataset_sha256,
    environment_metadata,
    fit_candidate,
    get_split_masks,
    load_forecast_dataset,
    load_frozen_bundle,
    load_json,
    predict_on_mask,
    resolve_dataset_path,
    save_json,
)
from forecasting.reliability import (
    clip_nonnegative,
    deterministic_predictions,
    predict_feature_frame,
    prediction_health,
    validate_feature_schema,
    validate_feature_values,
)
from forecasting.robustness import (
    affected_counts_by_split,
    artifact_manifest,
    boundary_partial_impact,
    compare_metrics,
    grouped_metrics,
    manifest_changed,
    mask_boundary_partial_history,
    outage_bounds,
    segment_payload,
    volume_quartile_edges,
)

DEFAULT_OUTPUT_DIR = Path("data/processed/phase6a")


def _capture_error(callable_: Callable[[], Any]) -> dict[str, Any]:
    try:
        callable_()
    except Exception as exc:  # noqa: BLE001 - report the validation behavior
        return {
            "raised": True,
            "type": type(exc).__name__,
            "message": str(exc),
        }
    return {"raised": False, "type": None, "message": None}


def reproduce_phase5b(
    frame: pd.DataFrame,
    masks: dict[str, pd.Series],
    predictions: dict[str, pd.Series],
    artifacts_dir: Path,
) -> dict[str, Any]:
    validation_expected = load_json(artifacts_dir / "validation_metrics.json")
    final_test_expected = load_json(artifacts_dir / "final_test_metrics.json")

    validation_mask = build_common_mask(
        frame, masks["validation_complete"], predictions
    )
    test_mask = build_common_mask(
        frame, masks["test_complete"], predictions
    )
    validation_actual = compare_metrics(frame, predictions, validation_mask)
    test_actual = compare_metrics(frame, predictions, test_mask)

    expected_validation_mae = validation_expected["candidates"][
        "hist_gradient_boosting"
    ]["mae"]
    expected_validation_rmse = validation_expected["candidates"][
        "hist_gradient_boosting"
    ]["rmse"]
    expected_test_mae = final_test_expected["selected_model_metrics"]["mae"]
    expected_test_rmse = final_test_expected["selected_model_metrics"]["rmse"]

    def delta(actual: float, expected: float) -> float:
        return float(abs(actual - expected))

    payload = {
        "validation": {
            "common_mask_n": int(validation_mask.sum()),
            "expected_common_mask_n": int(
                validation_expected["availability"]["common_mask_n"]
            ),
            "frozen_hgb": validation_actual["methods"]["frozen_hgb"],
            "lag_24": validation_actual["methods"]["lag_24"],
            "expected_frozen_hgb_mae": expected_validation_mae,
            "expected_frozen_hgb_rmse": expected_validation_rmse,
            "absolute_delta_mae": delta(
                validation_actual["methods"]["frozen_hgb"]["mae"],
                expected_validation_mae,
            ),
            "absolute_delta_rmse": delta(
                validation_actual["methods"]["frozen_hgb"]["rmse"],
                expected_validation_rmse,
            ),
        },
        "final_test": {
            "common_mask_n": int(test_mask.sum()),
            "expected_common_mask_n": int(
                final_test_expected["availability"]["common_mask_n"]
            ),
            "frozen_hgb": test_actual["methods"]["frozen_hgb"],
            "lag_24": test_actual["methods"]["lag_24"],
            "expected_frozen_hgb_mae": expected_test_mae,
            "expected_frozen_hgb_rmse": expected_test_rmse,
            "absolute_delta_mae": delta(
                test_actual["methods"]["frozen_hgb"]["mae"],
                expected_test_mae,
            ),
            "absolute_delta_rmse": delta(
                test_actual["methods"]["frozen_hgb"]["rmse"],
                expected_test_rmse,
            ),
        },
    }
    payload["reproduced"] = bool(
        payload["validation"]["common_mask_n"]
        == payload["validation"]["expected_common_mask_n"]
        and payload["final_test"]["common_mask_n"]
        == payload["final_test"]["expected_common_mask_n"]
        and payload["validation"]["absolute_delta_mae"] < 1e-6
        and payload["validation"]["absolute_delta_rmse"] < 1e-6
        and payload["final_test"]["absolute_delta_mae"] < 1e-6
        and payload["final_test"]["absolute_delta_rmse"] < 1e-6
    )
    return payload


def sensitivity_analysis(
    frame: pd.DataFrame,
    masks: dict[str, pd.Series],
    current_predictions: dict[str, pd.Series],
    config: dict[str, Any],
    affected: pd.Series,
    output_dir: Path,
    model: Any,
) -> dict[str, Any]:
    masked_source = mask_boundary_partial_history(frame)
    masked_frame = build_primary_features(
        masked_source, target_col=TARGET_COL
    ).df

    masked_frozen = predict_on_mask(
        model, masked_frame, masks["complete_target"]
    )
    sensitivity_model = fit_candidate(
        "hist_gradient_boosting",
        config["hyperparameters"],
        masked_frame,
        masks["train_complete"],
    )
    masked_retrained = predict_on_mask(
        sensitivity_model, masked_frame, masks["complete_target"]
    )

    joblib.dump(
        {
            "model": sensitivity_model,
            "feature_columns": list(PRIMARY_FEATURE_COLUMNS),
            "hyperparameters": config["hyperparameters"],
            "note": (
                "Phase 6A sensitivity-only model. Boundary/partial demand was "
                "masked from lag/rolling history. Not a replacement for the "
                "frozen Phase 5B model."
            ),
        },
        output_dir / "sensitivity_masked_history_model.joblib",
    )

    all_predictions = {
        "frozen_hgb_current": current_predictions["frozen_hgb"],
        "frozen_hgb_masked_history": masked_frozen,
        "hgb_retrained_masked_history": masked_retrained,
        "lag_24": current_predictions["lag_24"],
    }
    affected_labels = affected.map(
        {True: "affected", False: "unaffected"}
    )
    payload: dict[str, Any] = {}
    for split in ("validation", "test"):
        common_mask = build_common_mask(
            frame, masks[f"{split}_complete"], all_predictions
        )
        payload[split] = {
            "n_common_mask": int(common_mask.sum()),
            "overall": compare_metrics(
                frame,
                all_predictions,
                common_mask,
                model_name="frozen_hgb_current",
                baseline_name="lag_24",
            ),
            "by_boundary_partial_history": grouped_metrics(
                frame,
                all_predictions,
                common_mask,
                affected_labels,
                ["unaffected", "affected"],
                model_name="frozen_hgb_current",
                baseline_name="lag_24",
            ),
        }
    return payload


def reliability_checks(
    frame: pd.DataFrame,
    config: dict[str, Any],
    model: Any,
    predictions: dict[str, pd.Series],
    masks: dict[str, pd.Series],
) -> dict[str, Any]:
    sample = frame.loc[:, list(PRIMARY_FEATURE_COLUMNS)].head(24).copy()
    health = {
        split: prediction_health(
            predictions["frozen_hgb"].loc[masks[f"{split}_complete"]]
        )
        for split in ("train", "validation", "test")
    }
    health["all_complete"] = prediction_health(
        predictions["frozen_hgb"].loc[masks["complete_target"]]
    )

    deterministic = deterministic_predictions(model, sample, repeats=3)
    missing_column = _capture_error(
        lambda: validate_feature_schema(sample.drop(columns=["lag_2"]))
    )
    infinite_value = _capture_error(
        lambda: validate_feature_values(
            sample.assign(lag_2=np.inf)
        )
    )
    wrong_type = _capture_error(
        lambda: validate_feature_values(
            sample.assign(lag_2="not-a-number")
        )
    )

    all_nan = pd.DataFrame(
        np.nan, index=range(3), columns=list(PRIMARY_FEATURE_COLUMNS)
    )
    all_nan_prediction = predict_feature_frame(model, all_nan)

    extra = sample.copy()
    extra["unexpected_column"] = 1
    extra_predictions = predict_feature_frame(model, extra)
    base_predictions = predict_feature_frame(model, sample)

    unexpected_values: dict[str, Any] = {}
    for label, value in (
        ("negative_history", -1000.0),
        ("zero_history", 0.0),
        ("huge_history", 1e9),
    ):
        altered = sample.copy()
        altered["lag_2"] = value
        altered_predictions = predict_feature_frame(model, altered)
        unexpected_values[label] = {
            "input_value": value,
            "all_finite": bool(np.isfinite(altered_predictions).all()),
            "min_prediction": float(altered_predictions.min()),
            "max_prediction": float(altered_predictions.max()),
        }

    raw_predictions = predictions["frozen_hgb"].loc[
        masks["complete_target"]
    ].to_numpy(dtype=float)
    clipped = clip_nonnegative(raw_predictions)
    return {
        "feature_columns_match_frozen_config": (
            list(config["feature_columns"])
            == list(PRIMARY_FEATURE_COLUMNS)
        ),
        "prediction_health": health,
        "deterministic_inference": deterministic,
        "missing_feature_column": missing_column,
        "infinite_feature_value": infinite_value,
        "non_numeric_feature_value": wrong_type,
        "all_nan_feature_row": {
            "predictions_finite": bool(
                np.isfinite(all_nan_prediction).all()
            ),
            "min_prediction": float(all_nan_prediction.min()),
            "max_prediction": float(all_nan_prediction.max()),
        },
        "extra_columns_ignored": {
            "same_predictions": bool(
                np.array_equal(extra_predictions, base_predictions)
            )
        },
        "unexpected_numeric_values": unexpected_values,
        "nonnegative_handling": {
            "raw_min": float(np.min(raw_predictions)),
            "raw_negative_count": int((raw_predictions < 0).sum()),
            "clipped_min": float(np.min(clipped)),
            "clip_changed_predictions": bool(
                not np.array_equal(raw_predictions, clipped)
            ),
            "note": (
                "The frozen model is evaluated without post-hoc clipping. "
                "clip_nonnegative is available for a future serving layer."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", type=Path, default=None, help="Forecast dataset path"
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help="Directory containing the frozen Phase 5B artifacts",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for Phase 6A outputs",
    )
    args = parser.parse_args()

    artifacts_dir = args.artifacts_dir
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_before = artifact_manifest(artifacts_dir)
    config, bundle, config_path, model_path = load_frozen_bundle(artifacts_dir)
    model = bundle["model"]

    dataset_path = resolve_dataset_path(args.dataset)
    dataset_hash = dataset_sha256(dataset_path)
    if dataset_hash != config["dataset"]["sha256"]:
        raise ValueError(
            "Dataset SHA-256 does not match the frozen Phase 5B configuration"
        )

    raw = load_forecast_dataset(dataset_path)
    frame = build_model_frame(raw).df
    masks = get_split_masks(frame)
    predictions = {
        "frozen_hgb": predict_on_mask(
            model, frame, masks["complete_target"]
        ),
        "lag_24": seasonal_naive_lag24(
            frame, target_col=TARGET_COL
        ).predictions,
    }

    edges = volume_quartile_edges(frame, masks["train_complete"])
    impact = boundary_partial_impact(frame)
    affected = impact["affected_any_historical_feature"]
    outage = outage_bounds(frame)

    segmented = {
        "volume_quartile_edges": edges,
        "outage_bounds": outage,
        "splits": {
            "train_in_sample": segment_payload(
                frame,
                predictions,
                masks["train"],
                "train",
                edges,
                affected,
                in_sample=True,
            ),
            "validation": segment_payload(
                frame,
                predictions,
                masks["validation"],
                "validation",
                edges,
                affected,
            ),
            "test": segment_payload(
                frame,
                predictions,
                masks["test"],
                "test",
                edges,
                affected,
                outage=outage,
            ),
        },
    }

    quality = {
        "policy": (
            "Boundary/partial source hours are real observations but are "
            "excluded from the conservative robustness check because they are "
            "not verified complete-coverage hours. No demand value is zero-filled."
        ),
        "source_rows": impact["source_rows"],
        "affected_counts": affected_counts_by_split(affected, masks),
        "affected_counts_boundary_only": affected_counts_by_split(
            impact["affected_boundary_only"], masks
        ),
        "affected_counts_partial_only": affected_counts_by_split(
            impact["affected_partial_only"], masks
        ),
        "affected_feature_counts_all_rows": impact[
            "affected_feature_counts_all_rows"
        ],
        "sensitivity": sensitivity_analysis(
            frame,
            masks,
            predictions,
            config,
            affected,
            output_dir,
            model,
        ),
    }

    reproduction = reproduce_phase5b(
        frame, masks, predictions, artifacts_dir
    )
    reliability = reliability_checks(
        frame, config, model, predictions, masks
    )

    manifest_after = artifact_manifest(artifacts_dir)
    preservation = {
        "before": manifest_before,
        "after": manifest_after,
        "diff": manifest_changed(manifest_before, manifest_after),
    }

    metadata = {
        "phase": "6A",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_path": str(dataset_path),
        "dataset_sha256": dataset_hash,
        "frozen_artifacts_dir": str(artifacts_dir),
        "frozen_config_path": str(config_path),
        "frozen_model_path": str(model_path),
        "environment": environment_metadata(),
        "split_summary": {
            split: {
                "n_spine_hours": int(masks[split].sum()),
                "n_complete_targets": int(
                    masks[f"{split}_complete"].sum()
                ),
            }
            for split in ("train", "validation", "test")
        },
        "test_caveat": (
            "The test split was already inspected in Phase 5A. Phase 6A does "
            "not retune or replace the frozen Phase 5B model."
        ),
    }

    save_json(output_dir / "segmented_analysis.json", segmented)
    save_json(output_dir / "historical_feature_quality.json", quality)
    save_json(output_dir / "phase5b_reproduction.json", reproduction)
    save_json(output_dir / "reliability_checks.json", reliability)
    save_json(output_dir / "frozen_artifact_preservation.json", preservation)
    save_json(output_dir / "analysis_metadata.json", metadata)

    summary = {
        "phase5b_reproduced": reproduction["reproduced"],
        "validation_common_mask_n": reproduction["validation"][
            "common_mask_n"
        ],
        "test_common_mask_n": reproduction["final_test"]["common_mask_n"],
        "boundary_partial_affected": quality["affected_counts"],
        "outage_bounds": outage,
        "reliability": reliability,
        "frozen_artifacts_unchanged": preservation["diff"]["unchanged"],
        "outputs": sorted(path.name for path in output_dir.iterdir()),
    }
    save_json(output_dir / "phase6a_summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
