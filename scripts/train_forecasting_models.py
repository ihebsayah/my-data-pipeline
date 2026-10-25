#!/usr/bin/env python3
"""Phase 5B staged model search, validation, freeze, and final test.

Stages:
  search      Chronological CV inside the training split only.
  validate    Fit best candidates on train, compare on validation only.
  freeze      Select by validation, fit on train, write frozen artifacts.
  final-test  Evaluate the frozen model exactly once on the test split.

The final-test stage requires a frozen configuration and matching dataset hash.
It refuses to overwrite final_test_metrics.json unless explicitly allowed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
from forecasting.features import (
    PRIMARY_CUTOFF_HOURS,
    PRIMARY_FEATURE_COLUMNS,
)
from forecasting.modeling import (
    DEFAULT_ARTIFACT_DIR,
    N_JOBS,
    RANDOM_STATE,
    SEARCH_CV_SPLITS,
    TARGET_COL,
    baseline_predictions,
    build_model_frame,
    candidate_model_types,
    dataset_sha256,
    environment_metadata,
    evaluate_common_mask,
    evaluate_optimistic_lag1,
    feature_list_payload,
    fit_candidate,
    get_split_masks,
    improvement_vs_baseline,
    load_forecast_dataset,
    load_frozen_bundle,
    load_json,
    predict_on_mask,
    resolve_dataset_path,
    save_json,
    search_candidate,
    select_candidate,
    split_summary,
)

TEST_PREINSPECTION_CAVEAT = (
    "The test split was already inspected during Phase 5A/5A.1/5A.2/5A.3 "
    "baseline work. It is therefore not a pristine untouched holdout. "
    "Phase 5B freezes the model before final-test and does not tune or select "
    "using final test performance."
)


def _load_stage_data(dataset_arg: str | None) -> dict[str, Any]:
    dataset_path = resolve_dataset_path(dataset_arg)
    raw = load_forecast_dataset(dataset_path)
    result = build_model_frame(raw)
    frame = result.df
    masks = get_split_masks(frame)
    return {
        "dataset_path": dataset_path,
        "dataset_sha256": dataset_sha256(dataset_path),
        "raw": raw,
        "frame": frame,
        "masks": masks,
        "feature_metadata": result.metadata,
        "split_summary": split_summary(frame, masks),
    }


def _verify_dataset_hash(expected: str, actual: str) -> None:
    if expected != actual:
        raise ValueError(
            "Dataset SHA-256 does not match the frozen/search artifact. "
            f"expected={expected} actual={actual}"
        )


def _dataset_payload(
    dataset_path: Path, digest: str, frame: pd.DataFrame
) -> dict[str, Any]:
    return {
        "path": str(dataset_path),
        "sha256": digest,
        "n_rows": int(len(frame)),
        "n_complete_targets": int(
            frame["coverage_status"].eq("complete").sum()
        ),
        "start_utc": str(frame["hour_utc"].iloc[0]),
        "end_utc": str(frame["hour_utc"].iloc[-1]),
    }


def stage_search(args: argparse.Namespace) -> None:
    data = _load_stage_data(args.dataset)
    frame = data["frame"]
    masks = data["masks"]
    train_mask = masks["train_complete"]

    X_train = frame.loc[train_mask, list(PRIMARY_FEATURE_COLUMNS)]
    y_train = frame.loc[train_mask, TARGET_COL].astype(float)

    payload: dict[str, Any] = {
        "phase": "5B",
        "stage": "hyperparameter_search",
        "target": TARGET_COL,
        "prediction_time": "beginning of target UTC hour t",
        "information_cutoff_hours": PRIMARY_CUTOFF_HOURS,
        "target_coverage_filter": "complete",
        "feature_columns": list(PRIMARY_FEATURE_COLUMNS),
        "n_train_rows": int(len(X_train)),
        "split_summary": data["split_summary"],
        "dataset": _dataset_payload(
            data["dataset_path"], data["dataset_sha256"], frame
        ),
        "random_state": RANDOM_STATE,
        "n_jobs": N_JOBS,
        "cv_n_splits": SEARCH_CV_SPLITS,
        "environment": environment_metadata(),
        "models": {},
    }
    for model_type in candidate_model_types():
        print(f"[search] {model_type}: chronological CV on train only")
        payload["models"][model_type] = search_candidate(
            model_type, X_train, y_train
        )
        best = payload["models"][model_type]
        print(
            f"[search] {model_type}: best_cv_mae={best['best_cv_mae']:.3f} "
            f"params={best['best_params']} "
            f"({best['n_configurations']} configs)"
        )

    output_path = Path(args.artifacts_dir) / "hyperparameter_search.json"
    save_json(output_path, payload)
    print(f"[search] saved {output_path}")


def stage_validate(args: argparse.Namespace) -> None:
    artifacts_dir = Path(args.artifacts_dir)
    search = load_json(artifacts_dir / "hyperparameter_search.json")
    data = _load_stage_data(args.dataset)
    _verify_dataset_hash(search["dataset"]["sha256"], data["dataset_sha256"])

    frame = data["frame"]
    masks = data["masks"]
    train_mask = masks["train_complete"]
    validation_mask = masks["validation_complete"]
    n_eligible = int(validation_mask.sum())

    candidate_predictions: dict[str, pd.Series] = {}
    candidate_params: dict[str, dict[str, Any]] = {}
    for model_type in candidate_model_types():
        params = search["models"][model_type]["best_params"]
        candidate_params[model_type] = params
        fitted = fit_candidate(model_type, params, frame, train_mask)
        candidate_predictions[model_type] = predict_on_mask(
            fitted, frame, validation_mask
        )

    lag24 = baseline_predictions(frame)["lag_24"]
    all_predictions = {**candidate_predictions, "lag_24": lag24}
    common_mask, evaluated = evaluate_common_mask(
        frame, all_predictions, validation_mask, n_eligible
    )
    common_n = int(common_mask.sum())

    candidates_payload = {
        model_type: {
            "model_type": model_type,
            "hyperparameters": candidate_params[model_type],
            **evaluated[model_type],
        }
        for model_type in candidate_model_types()
    }
    baseline_payload = evaluated["lag_24"]
    improvements = {
        model_type: improvement_vs_baseline(
            evaluated[model_type], baseline_payload
        )
        for model_type in candidate_model_types()
    }
    availability = {
        "validation_complete_targets": n_eligible,
        "ridge_predictions_available": int(
            (validation_mask & candidate_predictions["ridge"].notna()).sum()
        ),
        "hist_gradient_boosting_predictions_available": int(
            (
                validation_mask
                & candidate_predictions["hist_gradient_boosting"].notna()
            ).sum()
        ),
        "lag_24_predictions_available": int(
            (validation_mask & lag24.notna()).sum()
        ),
        "common_mask_n": common_n,
    }

    selected, rationale = select_candidate({"candidates": evaluated})

    payload: dict[str, Any] = {
        "phase": "5B",
        "stage": "validation",
        "selection_partition_only": True,
        "test_used": False,
        "target": TARGET_COL,
        "target_coverage_filter": "complete",
        "feature_columns": list(PRIMARY_FEATURE_COLUMNS),
        "split_summary": data["split_summary"],
        "dataset": _dataset_payload(
            data["dataset_path"], data["dataset_sha256"], frame
        ),
        "validation_timestamps": {
            "start_utc": data["split_summary"]["validation"]["complete_start_utc"],
            "end_utc": data["split_summary"]["validation"]["complete_end_utc"],
        },
        "counts": {
            "validation_spine_hours": int(masks["validation"].sum()),
            "validation_complete_targets": n_eligible,
            "common_mask_n": common_n,
        },
        "availability": availability,
        "common_mask_definition": (
            "validation split & coverage_status == 'complete' & "
            "rides_total notna & lag_24 notna & ridge prediction notna & "
            "hist_gradient_boosting prediction notna. The optimistic lag-1 "
            "reference is excluded from this mask."
        ),
        "prediction_coverage_definition": (
            "numerator = timestamps evaluated on the common mask; "
            "denominator = complete-coverage validation targets "
            f"({n_eligible})."
        ),
        "candidates": candidates_payload,
        "baseline_lag24": baseline_payload,
        "improvements_vs_lag24": improvements,
        "optimistic_lag1_non_operational": evaluate_optimistic_lag1(
            frame, validation_mask, n_eligible
        ),
        "selection_rule": (
            "Lowest MAE on the validation common mask. Test data is not used."
        ),
        "tentative_selected_model": selected,
        "selection_rationale": rationale,
    }
    output_path = artifacts_dir / "validation_metrics.json"
    save_json(output_path, payload)

    print(f"[validate] validation complete targets: {n_eligible}")
    print(f"[validate] common mask n: {common_n}")
    for model_type in candidate_model_types():
        metrics = evaluated[model_type]
        print(
            f"[validate] {model_type}: MAE={metrics['mae']:.3f} "
            f"RMSE={metrics['rmse']:.3f} n={metrics['n_evaluated']}"
        )
    print(
        f"[validate] lag_24: MAE={baseline_payload['mae']:.3f} "
        f"RMSE={baseline_payload['rmse']:.3f} n={baseline_payload['n_evaluated']}"
    )
    print(f"[validate] tentative selection: {selected}")
    print(f"[validate] saved {output_path}")


def stage_freeze(args: argparse.Namespace) -> None:
    artifacts_dir = Path(args.artifacts_dir)
    search = load_json(artifacts_dir / "hyperparameter_search.json")
    validation = load_json(artifacts_dir / "validation_metrics.json")
    data = _load_stage_data(args.dataset)
    _verify_dataset_hash(search["dataset"]["sha256"], data["dataset_sha256"])
    if validation["dataset"]["sha256"] != data["dataset_sha256"]:
        raise ValueError("Validation artifact dataset hash does not match dataset")

    frame = data["frame"]
    masks = data["masks"]
    selected, rationale = select_candidate(validation)
    params = search["models"][selected]["best_params"]

    fitted = fit_candidate(selected, params, frame, masks["train_complete"])
    feature_list = feature_list_payload()
    save_json(artifacts_dir / "feature_list.json", feature_list)

    model_path = artifacts_dir / "final_model.joblib"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": fitted,
            "model_type": selected,
            "hyperparameters": params,
            "feature_columns": list(PRIMARY_FEATURE_COLUMNS),
            "target": TARGET_COL,
            "random_state": RANDOM_STATE,
            "dataset_sha256": data["dataset_sha256"],
            "trained_on": {
                "split": "train",
                "target_coverage_filter": "complete",
                "n_rows": int(masks["train_complete"].sum()),
                "start_utc": data["split_summary"]["train"]["complete_start_utc"],
                "end_utc": data["split_summary"]["train"]["complete_end_utc"],
            },
        },
        model_path,
    )

    selected_validation = validation["candidates"][selected]
    baseline_validation = validation["baseline_lag24"]
    config: dict[str, Any] = {
        "status": "frozen",
        "phase": "5B",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "target": TARGET_COL,
        "prediction_time": "beginning of target UTC hour t",
        "information_cutoff": {
            "latest_permitted_demand_hour_offset": -PRIMARY_CUTOFF_HOURS,
            "statement": "Latest permitted observed demand is t-2.",
            "lag_1_selected": False,
        },
        "feature_columns": list(PRIMARY_FEATURE_COLUMNS),
        "feature_metadata": data["feature_metadata"],
        "model_type": selected,
        "hyperparameters": params,
        "preprocessing": (
            {
                "imputer": "median",
                "imputer_keep_empty_features": True,
                "imputer_fit_on": "training split only (or CV training fold)",
                "scaler": "StandardScaler",
                "scaler_fit_on": "training split only (or CV training fold)",
                "estimator": "Ridge",
            }
            if selected == "ridge"
            else {
                "imputer": "none",
                "missing_value_handling": "HistGradientBoosting native NaN handling",
                "scaler": "none",
                "estimator": "HistGradientBoostingRegressor",
            }
        ),
        "missing_data_policy": (
            "No row compression or target fill. Feature NaNs are handled by the "
            "model-specific preprocessing fit on training data only. Training "
            "and evaluation targets are complete-coverage hours only."
        ),
        "evaluation_mask": {
            "coverage_status": "complete",
            "target_notna": True,
            "common_mask_with_lag24": True,
            "common_mask_definition": validation["common_mask_definition"],
        },
        "training": {
            "split": "train",
            "target_coverage_filter": "complete",
            "n_rows": int(masks["train_complete"].sum()),
            "cv": {
                "strategy": "TimeSeriesSplit",
                "n_splits": SEARCH_CV_SPLITS,
                "scoring": "neg_mean_absolute_error",
                "partition": "train only",
            },
        },
        "validation": {
            "n_complete_targets": validation["counts"][
                "validation_complete_targets"
            ],
            "common_mask_n": validation["counts"]["common_mask_n"],
            "selected_model_metrics": {
                "mae": selected_validation["mae"],
                "rmse": selected_validation["rmse"],
            },
            "lag24_metrics": {
                "mae": baseline_validation["mae"],
                "rmse": baseline_validation["rmse"],
            },
            "improvement_vs_lag24": validation["improvements_vs_lag24"][selected],
            "candidate_mae": {
                model_type: validation["candidates"][model_type]["mae"]
                for model_type in candidate_model_types()
            },
        },
        "selection_rationale": rationale,
        "selection_rule": validation["selection_rule"],
        "dataset": validation["dataset"],
        "random_state": RANDOM_STATE,
        "n_jobs": N_JOBS,
        "environment": environment_metadata(),
        "test_set_caveat": TEST_PREINSPECTION_CAVEAT,
        "final_test_guard": (
            "final-test requires status='frozen', matching dataset SHA-256, and "
            "refuses to overwrite final_test_metrics.json by default."
        ),
    }
    config_path = artifacts_dir / "frozen_model_config.json"
    save_json(config_path, config)

    print(f"[freeze] selected: {selected}")
    print(
        f"[freeze] validation MAE={selected_validation['mae']:.3f} "
        f"vs lag24 {baseline_validation['mae']:.3f} "
        f"(n={validation['counts']['common_mask_n']})"
    )
    print(f"[freeze] saved {model_path}")
    print(f"[freeze] saved {config_path}")
    print(f"[freeze] saved {artifacts_dir / 'feature_list.json'}")


def stage_final_test(args: argparse.Namespace) -> None:
    artifacts_dir = Path(args.artifacts_dir)
    output_path = artifacts_dir / "final_test_metrics.json"
    if output_path.exists() and not args.overwrite_final_test:
        raise FileExistsError(
            f"{output_path} already exists. The final test is one-shot; pass "
            "--overwrite-final-test only after documenting a post-test bug fix."
        )

    config, bundle, config_path, model_path = load_frozen_bundle(artifacts_dir)
    if bundle["model_type"] != config["model_type"]:
        raise ValueError("Saved model type does not match frozen configuration")
    if bundle["hyperparameters"] != config["hyperparameters"]:
        raise ValueError("Saved model hyperparameters do not match frozen configuration")
    data = _load_stage_data(args.dataset)
    _verify_dataset_hash(config["dataset"]["sha256"], data["dataset_sha256"])
    _verify_dataset_hash(bundle["dataset_sha256"], data["dataset_sha256"])

    frame = data["frame"]
    masks = data["masks"]
    test_mask = masks["test_complete"]
    n_eligible = int(test_mask.sum())

    selected_prediction = predict_on_mask(bundle["model"], frame, test_mask)
    lag24 = baseline_predictions(frame)["lag_24"]
    all_predictions = {"selected_model": selected_prediction, "lag_24": lag24}
    common_mask, evaluated = evaluate_common_mask(
        frame, all_predictions, test_mask, n_eligible
    )
    common_n = int(common_mask.sum())
    improvement = improvement_vs_baseline(
        evaluated["selected_model"], evaluated["lag_24"]
    )

    payload: dict[str, Any] = {
        "phase": "5B",
        "stage": "final_test",
        "one_final_evaluation": True,
        "selected_model": config["model_type"],
        "hyperparameters": config["hyperparameters"],
        "feature_columns": config["feature_columns"],
        "frozen_config_path": str(config_path),
        "final_model_path": str(model_path),
        "frozen_config_sha256": dataset_sha256(config_path),
        "final_model_sha256": dataset_sha256(model_path),
        "dataset": _dataset_payload(
            data["dataset_path"], data["dataset_sha256"], frame
        ),
        "counts": {
            "test_spine_hours": int(masks["test"].sum()),
            "test_complete_targets": n_eligible,
            "common_mask_n": common_n,
        },
        "availability": {
            "test_complete_targets": n_eligible,
            "selected_model_predictions_available": int(
                (test_mask & selected_prediction.notna()).sum()
            ),
            "lag_24_predictions_available": int(
                (test_mask & lag24.notna()).sum()
            ),
            "common_mask_n": common_n,
        },
        "common_mask_definition": (
            "test split & coverage_status == 'complete' & rides_total notna & "
            "lag_24 notna & selected model prediction notna. The optimistic "
            "lag-1 reference is excluded from this mask."
        ),
        "prediction_coverage_definition": (
            "numerator = timestamps evaluated on the common mask; "
            f"denominator = complete-coverage test targets ({n_eligible})."
        ),
        "selected_model_metrics": evaluated["selected_model"],
        "baseline_lag24_metrics": evaluated["lag_24"],
        "improvement_vs_lag24": improvement,
        "optimistic_lag1_non_operational": evaluate_optimistic_lag1(
            frame, test_mask, n_eligible
        ),
        "selection_rationale": config["selection_rationale"],
        "selection_rule": config["selection_rule"],
        "validation_reference": config["validation"],
        "test_set_preinspection_caveat": TEST_PREINSPECTION_CAVEAT,
        "test_used_for_selection": False,
    }
    save_json(output_path, payload)

    selected_metrics = evaluated["selected_model"]
    baseline_metrics = evaluated["lag_24"]
    print(f"[final-test] selected model: {config['model_type']}")
    print(f"[final-test] test complete targets: {n_eligible}")
    print(f"[final-test] common mask n: {common_n}")
    print(
        f"[final-test] selected: MAE={selected_metrics['mae']:.3f} "
        f"RMSE={selected_metrics['rmse']:.3f} n={selected_metrics['n_evaluated']}"
    )
    print(
        f"[final-test] lag_24: MAE={baseline_metrics['mae']:.3f} "
        f"RMSE={baseline_metrics['rmse']:.3f} n={baseline_metrics['n_evaluated']}"
    )
    print(
        f"[final-test] improvement vs lag_24: "
        f"MAE {improvement['mae_absolute']:.3f} "
        f"({improvement['mae_percent']:.2f}%), "
        f"RMSE {improvement['rmse_absolute']:.3f} "
        f"({improvement['rmse_percent']:.2f}%)"
    )
    print(f"[final-test] saved {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 5B forecasting model workflow"
    )
    parser.add_argument(
        "stage",
        choices=["search", "validate", "freeze", "final-test"],
        help="Workflow stage to run.",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help=(
            "Dataset path. Defaults to data/processed/forecast_dataset.parquet "
            "and falls back to CSV."
        ),
    )
    parser.add_argument(
        "--artifacts-dir",
        default=str(DEFAULT_ARTIFACT_DIR),
        help="Directory for local Phase 5B artifacts (gitignored).",
    )
    parser.add_argument(
        "--overwrite-final-test",
        action="store_true",
        help=(
            "Allow overwriting an existing final_test_metrics.json. Use only "
            "after documenting a post-test bug fix."
        ),
    )
    args = parser.parse_args()

    if args.stage == "search":
        stage_search(args)
    elif args.stage == "validate":
        stage_validate(args)
    elif args.stage == "freeze":
        stage_freeze(args)
    elif args.stage == "final-test":
        stage_final_test(args)
    else:  # pragma: no cover - argparse enforces choices
        raise ValueError(f"Unknown stage: {args.stage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
