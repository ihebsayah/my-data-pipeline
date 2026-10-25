from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from .features import PRIMARY_FEATURE_COLUMNS


class FeatureSchemaError(ValueError):
    """The prediction frame does not match the frozen feature contract."""


class NonFiniteFeatureError(ValueError):
    """The prediction frame contains infinite values."""


def validate_feature_schema(
    frame: pd.DataFrame,
    expected_columns: Sequence[str] = PRIMARY_FEATURE_COLUMNS,
    allow_extra: bool = True,
) -> pd.DataFrame:
    """Select and order the frozen feature columns, rejecting missing features."""
    expected = list(expected_columns)
    missing = [column for column in expected if column not in frame.columns]
    if missing:
        raise FeatureSchemaError(
            f"Missing required feature columns: {missing}"
        )

    extra = [column for column in frame.columns if column not in expected]
    if extra and not allow_extra:
        raise FeatureSchemaError(
            f"Unexpected feature columns: {extra}"
        )

    return frame.loc[:, expected].copy()


def validate_feature_values(
    frame: pd.DataFrame,
    expected_columns: Sequence[str] = PRIMARY_FEATURE_COLUMNS,
) -> pd.DataFrame:
    """Validate schema and reject infinite values while permitting NaN inputs."""
    selected = validate_feature_schema(frame, expected_columns)
    try:
        values = selected.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Feature frame contains non-numeric values that cannot be cast to float"
        ) from exc

    if np.isinf(values).any():
        raise NonFiniteFeatureError(
            "Feature frame contains infinite values; the frozen model is not "
            "defined for inf. Use NaN for missing history."
        )
    return selected


def predict_feature_frame(
    model: Any,
    frame: pd.DataFrame,
    clamp_nonnegative: bool = False,
) -> np.ndarray:
    """Predict after schema/finite validation; NaN is allowed."""
    selected = validate_feature_values(frame)
    predictions = np.asarray(model.predict(selected), dtype=float)
    if not np.isfinite(predictions).all():
        raise RuntimeError("Model produced a non-finite prediction")
    if clamp_nonnegative:
        predictions = clip_nonnegative(predictions)
    return predictions


def clip_nonnegative(predictions: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(predictions), dtype=float)
    return np.maximum(values, 0.0)


def prediction_health(predictions: Iterable[float]) -> dict[str, Any]:
    values = np.asarray(list(predictions), dtype=float)
    finite = values[np.isfinite(values)]
    return {
        "n": int(values.size),
        "n_nonfinite": int((~np.isfinite(values)).sum()),
        "n_negative": int((finite < 0).sum()),
        "min": float(finite.min()) if finite.size else None,
        "max": float(finite.max()) if finite.size else None,
        "mean": float(finite.mean()) if finite.size else None,
    }


def deterministic_predictions(
    model: Any,
    frame: pd.DataFrame,
    repeats: int = 2,
) -> dict[str, Any]:
    if repeats < 2:
        raise ValueError("repeats must be at least 2")
    baseline = predict_feature_frame(model, frame)
    matches = []
    for _ in range(repeats - 1):
        candidate = predict_feature_frame(model, frame)
        matches.append(bool(np.array_equal(baseline, candidate, equal_nan=True)))
    return {
        "repeats": repeats,
        "all_identical": all(matches),
        "per_repeat_identical": matches,
    }
