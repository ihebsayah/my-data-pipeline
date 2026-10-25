from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .baselines import naive_previous_hour, seasonal_naive_lag24
from .evaluation import evaluate_by_segment, get_chronological_splits
from .features import (
    PRIMARY_FEATURE_COLUMNS,
    PRIMARY_CUTOFF_HOURS,
    FeatureBuildResult,
    assert_primary_feature_contract,
    build_primary_features,
    validate_continuous_hourly_spine,
)

TARGET_COL = "rides_total"
PRIMARY_COVERAGE_STATUS = "complete"
RANDOM_STATE = 42
SEARCH_CV_SPLITS = 4
N_JOBS = 1
DEFAULT_ARTIFACT_DIR = Path("data/processed/phase5b")
DEFAULT_DATASET_PATH = Path("data/processed/forecast_dataset.parquet")

RIDGE_PARAM_GRID: dict[str, list[Any]] = {
    "alpha": [0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0],
}
HGB_PARAM_GRID: dict[str, list[Any]] = {
    "learning_rate": [0.03, 0.05, 0.1],
    "max_iter": [200, 400],
    "max_leaf_nodes": [15, 31],
}

EXCLUDED_FEATURE_REASONS: dict[str, str] = {
    "rides_total": "Target; never a predictor.",
    "rides_member": "Target component; ex-post information.",
    "rides_casual": "Target component; ex-post information.",
    "coverage_status": "Coverage/audit flag; not a predictor.",
    "coverage_reason": "Coverage/audit flag; not a predictor.",
    "in_demand_mart": "Coverage/audit flag; not a predictor.",
    "is_source_truncated_x": "Coverage/audit flag; not a predictor.",
    "is_source_truncated_y": "Coverage/audit flag; not a predictor.",
    "is_dst_ambiguous_hour": "Coverage/audit flag; not a predictor.",
    "in_outage_window": "Coverage/audit flag; not a predictor.",
    "hour_utc": "Time identifier; calendar features derived from it are used.",
    "hour_local_start": "Local-time identifier; not a predictor.",
    "hour_local_date": "Local date identifier; not a predictor.",
    "lag_1": "Prohibited: violates conservative t-2 information cutoff.",
    "roll_mean_24": "Phase 5A column uses shift(1); unsafe for t-2 cutoff.",
    "roll_std_24": "Phase 5A column uses shift(1); unsafe for t-2 cutoff.",
    "roll_min_24": "Phase 5A column uses shift(1); unsafe for t-2 cutoff.",
    "roll_max_24": "Phase 5A column uses shift(1); unsafe for t-2 cutoff.",
    "roll_mean_168": "Phase 5A column uses shift(1); unsafe for t-2 cutoff.",
    "roll_std_168": "Phase 5A column uses shift(1); unsafe for t-2 cutoff.",
    "roll_min_168": "Phase 5A column uses shift(1); unsafe for t-2 cutoff.",
    "roll_max_168": "Phase 5A column uses shift(1); unsafe for t-2 cutoff.",
}


# ---------------------------------------------------------------------------
# Data loading / feature framing
# ---------------------------------------------------------------------------


def resolve_dataset_path(path: Optional[str | Path] = None) -> Path:
    """Resolve the dataset path, falling back from parquet to CSV."""
    if path is not None:
        candidate = Path(path)
        if candidate.suffix == ".parquet" and not candidate.exists():
            candidate = candidate.with_suffix(".csv")
        if not candidate.exists():
            raise FileNotFoundError(f"Forecast dataset not found: {candidate}")
        return candidate

    parquet_path = DEFAULT_DATASET_PATH
    if parquet_path.exists():
        return parquet_path
    csv_path = parquet_path.with_suffix(".csv")
    if csv_path.exists():
        return csv_path
    raise FileNotFoundError(
        f"Forecast dataset not found at {parquet_path} or {csv_path}"
    )


def dataset_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_forecast_dataset(path: Optional[str | Path] = None) -> pd.DataFrame:
    """Load the Phase 5A dataset and enforce the continuous UTC hourly spine."""
    resolved = resolve_dataset_path(path)
    if resolved.suffix == ".parquet":
        df = pd.read_parquet(resolved)
    else:
        df = pd.read_csv(resolved)

    if "hour_utc" not in df.columns:
        raise ValueError("Dataset is missing hour_utc")
    df["hour_utc"] = pd.to_datetime(df["hour_utc"], utc=True)
    df = df.sort_values("hour_utc").reset_index(drop=True)
    validate_continuous_hourly_spine(df)
    return df


def build_model_frame(df: pd.DataFrame) -> FeatureBuildResult:
    """Build the conservative primary feature frame and required eval columns."""
    result = build_primary_features(df, target_col=TARGET_COL)
    required = {
        "hour_utc",
        TARGET_COL,
        "coverage_status",
        "hour_local",
        "is_weekend_local",
    }
    missing = required - set(result.df.columns)
    if missing:
        raise ValueError(f"Model frame is missing required columns: {sorted(missing)}")
    if len(result.df) != len(df):
        raise ValueError("Primary feature build must preserve the hourly spine length")
    return result


def get_split_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Return chronological split masks plus complete-target masks."""
    train, validation, test = get_chronological_splits(frame)
    complete_target = frame["coverage_status"].eq(
        PRIMARY_COVERAGE_STATUS
    ) & frame[TARGET_COL].notna()

    masks = {
        "train": train,
        "validation": validation,
        "test": test,
        "complete_target": complete_target,
        "train_complete": train & complete_target,
        "validation_complete": validation & complete_target,
        "test_complete": test & complete_target,
    }
    for name, mask in masks.items():
        if mask.index.tolist() != frame.index.tolist():
            raise ValueError(f"Mask {name} is not aligned with frame index")
    return masks


def split_summary(frame: pd.DataFrame, masks: dict[str, pd.Series]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for split in ("train", "validation", "test"):
        split_mask = masks[split]
        complete_mask = masks[f"{split}_complete"]
        summary[split] = {
            "n_spine_hours": int(split_mask.sum()),
            "n_complete_targets": int(complete_mask.sum()),
            "start_utc": (
                str(frame.loc[split_mask, "hour_utc"].iloc[0])
                if split_mask.any()
                else None
            ),
            "end_utc": (
                str(frame.loc[split_mask, "hour_utc"].iloc[-1])
                if split_mask.any()
                else None
            ),
            "complete_start_utc": (
                str(frame.loc[complete_mask, "hour_utc"].iloc[0])
                if complete_mask.any()
                else None
            ),
            "complete_end_utc": (
                str(frame.loc[complete_mask, "hour_utc"].iloc[-1])
                if complete_mask.any()
                else None
            ),
        }
    return summary


# ---------------------------------------------------------------------------
# Candidate models
# ---------------------------------------------------------------------------


def make_ridge_pipeline() -> Pipeline:
    """Ridge with train-fold median imputation and standard scaling."""
    return Pipeline(
        steps=[
            (
                "imputer",
                SimpleImputer(strategy="median", keep_empty_features=True),
            ),
            ("scaler", StandardScaler()),
            ("model", Ridge(random_state=RANDOM_STATE)),
        ]
    )


def make_hgb_pipeline() -> Pipeline:
    """HistGradientBoosting with native NaN handling and no explicit imputer."""
    return Pipeline(
        steps=[
            (
                "model",
                HistGradientBoostingRegressor(
                    loss="squared_error",
                    early_stopping=False,
                    random_state=RANDOM_STATE,
                ),
            )
        ]
    )


def make_pipeline(model_type: str) -> Pipeline:
    if model_type == "ridge":
        return make_ridge_pipeline()
    if model_type == "hist_gradient_boosting":
        return make_hgb_pipeline()
    raise ValueError(f"Unsupported model type: {model_type}")


def candidate_grid(model_type: str) -> dict[str, list[Any]]:
    if model_type == "ridge":
        return RIDGE_PARAM_GRID
    if model_type == "hist_gradient_boosting":
        return HGB_PARAM_GRID
    raise ValueError(f"Unsupported model type: {model_type}")


def candidate_model_types() -> tuple[str, ...]:
    return ("ridge", "hist_gradient_boosting")


def _pipeline_params(params: dict[str, Any]) -> dict[str, Any]:
    return {f"model__{key}": value for key, value in params.items()}


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key.removeprefix("model__"): value
        for key, value in params.items()
    }


def search_candidate(
    model_type: str,
    X_train: pd.DataFrame,
    y_train: pd.Series,
) -> dict[str, Any]:
    """Chronological CV search inside the training split only."""
    if len(X_train) == 0:
        raise ValueError("Cannot search on an empty training set")
    pipeline = make_pipeline(model_type)
    search = GridSearchCV(
        estimator=pipeline,
        param_grid=_pipeline_params(candidate_grid(model_type)),
        scoring="neg_mean_absolute_error",
        cv=TimeSeriesSplit(n_splits=SEARCH_CV_SPLITS),
        n_jobs=N_JOBS,
        refit=True,
        return_train_score=False,
        error_score="raise",
    )
    search.fit(X_train, y_train)

    cv_results = search.cv_results_
    configurations = []
    for index, params in enumerate(cv_results["params"]):
        configurations.append(
            {
                "params": _clean_params(params),
                "mean_cv_mae": float(-cv_results["mean_test_score"][index]),
                "std_cv_mae": float(cv_results["std_test_score"][index]),
                "rank": int(cv_results["rank_test_score"][index]),
            }
        )
    configurations.sort(key=lambda item: (item["rank"], item["mean_cv_mae"]))

    return {
        "model_type": model_type,
        "n_configurations": len(configurations),
        "param_grid": candidate_grid(model_type),
        "cv": {
            "strategy": "TimeSeriesSplit",
            "n_splits": SEARCH_CV_SPLITS,
            "scoring": "neg_mean_absolute_error",
            "shuffle": False,
            "selection_partition": "train only",
        },
        "n_train_rows": int(len(X_train)),
        "best_params": _clean_params(search.best_params_),
        "best_cv_mae": float(-search.best_score_),
        "configurations": configurations,
    }


def fit_candidate(
    model_type: str,
    params: dict[str, Any],
    frame: pd.DataFrame,
    train_mask: pd.Series,
) -> Pipeline:
    """Fit a candidate on complete-coverage training rows only."""
    if not bool(train_mask.any()):
        raise ValueError("Training mask selects zero rows")
    pipeline = make_pipeline(model_type)
    pipeline.set_params(**_pipeline_params(params))
    X_train = frame.loc[train_mask, list(PRIMARY_FEATURE_COLUMNS)]
    y_train = frame.loc[train_mask, TARGET_COL].astype(float)
    pipeline.fit(X_train, y_train)
    return pipeline


def predict_on_mask(
    pipeline: Pipeline,
    frame: pd.DataFrame,
    mask: pd.Series,
) -> pd.Series:
    """Predict only where requested; all other rows stay NaN."""
    predictions = pd.Series(np.nan, index=frame.index, dtype=float)
    if bool(mask.any()):
        X = frame.loc[mask, list(PRIMARY_FEATURE_COLUMNS)]
        predictions.loc[mask] = pipeline.predict(X)
    return predictions


# ---------------------------------------------------------------------------
# Fair evaluation
# ---------------------------------------------------------------------------


def regression_metrics(y_true: pd.Series, y_pred: pd.Series) -> dict[str, Any]:
    y_true_array = np.asarray(y_true, dtype=float)
    y_pred_array = np.asarray(y_pred, dtype=float)
    if len(y_true_array) != len(y_pred_array):
        raise ValueError("y_true and y_pred length mismatch")
    if len(y_true_array) == 0:
        return {"mae": float("nan"), "rmse": float("nan"), "n": 0}
    errors = y_true_array - y_pred_array
    return {
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "n": int(len(y_true_array)),
    }


def build_common_mask(
    frame: pd.DataFrame,
    split_mask: pd.Series,
    predictions: dict[str, pd.Series],
) -> pd.Series:
    """Identical evaluation mask for every compared prediction series.

    The mask is formed from the split, complete-coverage target availability,
    and non-missing predictions for every method in ``predictions``.  The
    optimistic lag-1 reference is intentionally not accepted here.
    """
    mask = split_mask.copy()
    mask &= frame[TARGET_COL].notna()
    for name, prediction in predictions.items():
        if name == "lag_1_optimistic":
            raise ValueError("Optimistic lag-1 must not enter the fair common mask")
        if not prediction.index.equals(frame.index):
            raise ValueError(f"Prediction series {name} is not aligned with frame")
        mask &= prediction.notna()
    return mask.fillna(False).astype(bool)


def evaluate_predictions_on_mask(
    frame: pd.DataFrame,
    predictions: dict[str, pd.Series],
    mask: pd.Series,
    n_eligible_targets: int,
) -> dict[str, dict[str, Any]]:
    """Evaluate each method on exactly the supplied mask."""
    if mask.sum() == 0:
        raise ValueError("Evaluation mask selects zero rows")
    evaluated: dict[str, dict[str, Any]] = {}
    for name, prediction in predictions.items():
        segmented = evaluate_by_segment(
            frame, prediction, frame[TARGET_COL], mask
        )
        overall = segmented["overall"]
        evaluated[name] = {
            "mae": overall["mae"],
            "rmse": overall["rmse"],
            "n_evaluated": overall["n"],
            "prediction_coverage": {
                "numerator": overall["n"],
                "denominator": int(n_eligible_targets),
                "fraction": (
                    float(overall["n"] / n_eligible_targets)
                    if n_eligible_targets
                    else float("nan")
                ),
            },
            "weekday": segmented.get("weekday"),
            "weekend": segmented.get("weekend"),
            "by_hour": segmented.get("by_hour", {}),
        }
    return evaluated


def improvement_vs_baseline(
    candidate: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    """Positive values mean the candidate is better (lower error) than lag-24."""
    mae_improvement = baseline["mae"] - candidate["mae"]
    rmse_improvement = baseline["rmse"] - candidate["rmse"]
    return {
        "mae_absolute": float(mae_improvement),
        "mae_percent": (
            float(100.0 * mae_improvement / baseline["mae"])
            if baseline["mae"]
            else float("nan")
        ),
        "rmse_absolute": float(rmse_improvement),
        "rmse_percent": (
            float(100.0 * rmse_improvement / baseline["rmse"])
            if baseline["rmse"]
            else float("nan")
        ),
        "sign_convention": "positive = model better (lower error than lag-24)",
    }


def evaluate_common_mask(
    frame: pd.DataFrame,
    predictions: dict[str, pd.Series],
    split_mask: pd.Series,
    n_eligible_targets: int,
) -> tuple[pd.Series, dict[str, dict[str, Any]]]:
    common_mask = build_common_mask(frame, split_mask, predictions)
    evaluated = evaluate_predictions_on_mask(
        frame, predictions, common_mask, n_eligible_targets
    )
    return common_mask, evaluated


def evaluate_optimistic_lag1(
    frame: pd.DataFrame,
    split_mask: pd.Series,
    n_eligible_targets: int,
) -> dict[str, Any]:
    """Non-operational lag-1 reference on its own available timestamps."""
    lag1 = naive_previous_hour(frame, target_col=TARGET_COL)
    available_mask = split_mask & frame[TARGET_COL].notna() & lag1.predictions.notna()
    evaluated = evaluate_predictions_on_mask(
        frame,
        {"lag_1_optimistic": lag1.predictions},
        available_mask,
        n_eligible_targets,
    )["lag_1_optimistic"]
    return {
        "label": (
            "NON-OPERATIONAL: optimistic lag-1 reference. It assumes demand(t-1) "
            "is available at the start of target hour t. It is not part of the "
            "common mask and is not used for model selection."
        ),
        **evaluated,
    }


def baseline_predictions(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "lag_24": seasonal_naive_lag24(frame, target_col=TARGET_COL).predictions,
    }


# ---------------------------------------------------------------------------
# Selection / artifacts
# ---------------------------------------------------------------------------


def select_candidate(validation_metrics: dict[str, Any]) -> tuple[str, str]:
    """Select the candidate with the lowest validation common-mask MAE."""
    candidates = validation_metrics["candidates"]
    ordered = sorted(
        candidates.items(),
        key=lambda item: (
            item[1]["mae"],
            0 if item[0] == "ridge" else 1,
        ),
    )
    selected, best = ordered[0]
    runner_up, second = ordered[1]
    rationale = (
        f"Selected {selected} because it has the lowest validation common-mask "
        f"MAE ({best['mae']:.3f}) versus {runner_up} "
        f"({second['mae']:.3f}); selection used validation only."
    )
    return selected, rationale


def feature_list_payload() -> dict[str, Any]:
    included = list(PRIMARY_FEATURE_COLUMNS)
    calendar = [
        column
        for column in included
        if not column.startswith(("lag_", "roll2_"))
    ]
    historical = [
        column for column in included if column.startswith(("lag_", "roll2_"))
    ]
    excluded = [
        {"column": column, "reason": reason}
        for column, reason in EXCLUDED_FEATURE_REASONS.items()
    ]
    return {
        "target": TARGET_COL,
        "prediction_time": "beginning of target UTC hour t",
        "information_cutoff": {
            "latest_permitted_demand_hour_offset": -PRIMARY_CUTOFF_HOURS,
            "statement": "Latest permitted observed demand is t-2.",
            "lag_1_selected": False,
            "rolling_shift": PRIMARY_CUTOFF_HOURS,
        },
        "included_features": included,
        "included_by_group": {
            "calendar": calendar,
            "historical_demand": historical,
        },
        "excluded_features": excluded,
        "missing_data_policy": (
            "Missing hours remain rows with NaN target/history. No forward-fill, "
            "backward-fill, interpolation, or row compression. Ridge uses a "
            "train-only median imputer; HGB uses native NaN handling."
        ),
        "target_coverage_filter": PRIMARY_COVERAGE_STATUS,
    }


def save_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def environment_metadata() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sklearn": sklearn.__version__,
        "pandas": pd.__version__,
        "numpy": np.__version__,
    }


def load_frozen_bundle(
    artifacts_dir: str | Path = DEFAULT_ARTIFACT_DIR,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    """Load the frozen config/model, failing if the freeze stage has not run."""
    directory = Path(artifacts_dir)
    config_path = directory / "frozen_model_config.json"
    model_path = directory / "final_model.joblib"
    if not config_path.exists():
        raise FileNotFoundError(
            "Frozen configuration not found. Run the freeze stage before final-test."
        )
    if not model_path.exists():
        raise FileNotFoundError(
            "Frozen final_model.joblib not found. Run the freeze stage before final-test."
        )
    config = load_json(config_path)
    if config.get("status") != "frozen":
        raise ValueError("frozen_model_config.json does not have status='frozen'")
    assert_primary_feature_contract(config["feature_columns"])
    if list(config["feature_columns"]) != list(PRIMARY_FEATURE_COLUMNS):
        raise ValueError("Frozen feature list does not match the primary contract")
    bundle = joblib.load(model_path)
    if list(bundle["feature_columns"]) != list(PRIMARY_FEATURE_COLUMNS):
        raise ValueError("Saved model feature list does not match the primary contract")
    return config, bundle, config_path, model_path
