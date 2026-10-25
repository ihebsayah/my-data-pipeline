from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class BaselineResult:
    predictions: pd.Series
    mask_available: pd.Series  # True if prediction available
    method: str


def naive_previous_hour(
    df: pd.DataFrame,
    target_col: str = "rides_total",
    require_lag1_available: bool = True,
) -> BaselineResult:
    """Previous-hour naive forecast - uses lag_1 from observed demand."""
    preds = df[target_col].shift(1)
    # Available only if previous hour exists and is available (not NaN)
    mask = preds.notna() if require_lag1_available else preds.notna()
    method = "naive_previous_hour" if require_lag1_available else "naive_previous_hour_optimistic"
    return BaselineResult(predictions=preds, mask_available=mask, method=method)


def seasonal_naive_lag24(df: pd.DataFrame, target_col: str = "rides_total") -> BaselineResult:
    """Seasonal naive using demand 24 hours ago."""
    preds = df[target_col].shift(24)
    mask = preds.notna()
    return BaselineResult(predictions=preds, mask_available=mask, method="seasonal_naive_lag24")


def seasonal_naive_lag168(df: pd.DataFrame, target_col: str = "rides_total") -> BaselineResult:
    """Seasonal naive using demand 168 hours ago."""
    preds = df[target_col].shift(168)
    mask = preds.notna()
    return BaselineResult(predictions=preds, mask_available=mask, method="seasonal_naive_lag168")


def historical_mean_past(
    df: pd.DataFrame,
    target_col: str = "rides_total",
    min_periods: int = 24,
) -> BaselineResult:
    """Simple historical mean using only past observations."""
    # expanding mean of past values (shift to exclude current)
    past_mean = df[target_col].shift(1).expanding(min_periods=min_periods).mean()
    mask = past_mean.notna()
    return BaselineResult(predictions=past_mean, mask_available=mask, method="historical_mean_past")


def naive_previous_hour_conservative(
    df: pd.DataFrame,
    target_col: str = "rides_total",
) -> BaselineResult:
    """Conservative scenario that does not require lag-1 availability."""
    # In conservative scenario, lag-1 not required - fall back to lag-24 or mark unavailable
    # But per requirement: add a conservative scenario that does not require lag-1
    preds = df[target_col].shift(24)  # fallback to seasonal naive
    mask = preds.notna()
    return BaselineResult(predictions=preds, mask_available=mask, method="naive_conservative_no_lag1")


def evaluate_baseline(
    df: pd.DataFrame,
    baseline: BaselineResult,
    target_col: str = "rides_total",
    eligible_mask: Optional[pd.Series] = None,
) -> dict:
    """Evaluate baseline on identical eligible target timestamps."""
    # Only evaluate where prediction is available and target is available
    eval_mask = baseline.mask_available & df[target_col].notna()
    if eligible_mask is not None:
        eval_mask = eval_mask & eligible_mask

    y_true = df.loc[eval_mask, target_col].astype(float)
    y_pred = baseline.predictions.loc[eval_mask].astype(float)

    if len(y_true) == 0:
        return {
            "method": baseline.method,
            "mae": np.nan,
            "rmse": np.nan,
            "n_evaluated": 0,
            "n_missing_predictions": int((~baseline.mask_available).sum()),
            "coverage": 0.0,
            "n_total": int(len(df)),
        }

    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    return {
        "method": baseline.method,
        "mae": mae,
        "rmse": rmse,
        "n_evaluated": int(len(y_true)),
        "n_missing_predictions": int((~baseline.mask_available).sum()),
        "n_unavailable_targets": int((baseline.mask_available & df[target_col].isna()).sum()),
        "coverage": float(len(y_true) / max(1, int((eligible_mask.sum() if eligible_mask is not None else len(df))))),
        "n_eligible_targets": int((eligible_mask.sum() if eligible_mask is not None else len(df[df[target_col].notna()]))),
    }
