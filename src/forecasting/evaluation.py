from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def get_chronological_splits(
    df: pd.DataFrame,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
    test_frac: float = 0.15,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Fixed chronological splits, no shuffle."""
    n = len(df)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    train_mask = pd.Series(False, index=df.index)
    val_mask = pd.Series(False, index=df.index)
    test_mask = pd.Series(False, index=df.index)
    train_mask.iloc[:train_end] = True
    val_mask.iloc[train_end:val_end] = True
    test_mask.iloc[val_end:] = True
    return train_mask, val_mask, test_mask


def evaluate_by_segment(
    df: pd.DataFrame,
    y_pred: pd.Series,
    y_true: pd.Series,
    mask_eval: pd.Series,
) -> dict:
    results = {}
    eval_df = df[mask_eval].copy()
    eval_df["_pred"] = y_pred[mask_eval].values
    eval_df["_true"] = y_true[mask_eval].values

    # Overall
    results["overall"] = {
        "mae": float(np.mean(np.abs(eval_df["_true"] - eval_df["_pred"]))),
        "rmse": float(np.sqrt(np.mean((eval_df["_true"] - eval_df["_pred"]) ** 2))),
        "n": int(len(eval_df)),
    }

    # By weekday/weekend
    if "is_weekend_local" in eval_df.columns:
        for key, grp in eval_df.groupby(eval_df["is_weekend_local"].astype(bool)):
            label = "weekend" if key else "weekday"
            results[f"{label}"] = {
                "mae": float(np.mean(np.abs(grp["_true"] - grp["_pred"]))),
                "rmse": float(np.sqrt(np.mean((grp["_true"] - grp["_pred"]) ** 2))),
                "n": int(len(grp)),
            }

    # By hour of day
    if "hour_local" in eval_df.columns:
        hour_results = {}
        for hour, grp in eval_df.groupby("hour_local"):
            hour_results[int(hour)] = {
                "mae": float(np.mean(np.abs(grp["_true"] - grp["_pred"]))),
                "rmse": float(np.sqrt(np.mean((grp["_true"] - grp["_pred"]) ** 2))),
                "n": int(len(grp)),
            }
        results["by_hour"] = hour_results

    return results
