#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from forecasting.baselines import (
    evaluate_baseline,
    historical_mean_past,
    naive_previous_hour,
    naive_previous_hour_conservative,
    seasonal_naive_lag168,
    seasonal_naive_lag24,
)
from forecasting.config import ForecastingConfig
from forecasting.contract import CONTRACT
from forecasting.evaluation import evaluate_by_segment, get_chronological_splits


def main():
    parser = argparse.ArgumentParser(description="Run Phase 5A baselines")
    parser.add_argument("--dataset", default="data/processed/forecast_dataset.parquet")
    parser.add_argument("--output", default="data/processed/baseline_results_phase5a1.json")
    args = parser.parse_args()

    config = ForecastingConfig()
    try:
        df = pd.read_parquet(args.dataset)
    except Exception:
        csv_path = Path(args.dataset).with_suffix(".csv")
        df = pd.read_csv(csv_path)
        # Fix tz
        if "hour_utc" in df.columns:
            df["hour_utc"] = pd.to_datetime(df["hour_utc"], utc=True)

    # Ensure hour_utc is tz-aware
    if not pd.api.types.is_datetime64_any_dtype(df["hour_utc"]):
        df["hour_utc"] = pd.to_datetime(df["hour_utc"], utc=True)
    elif df["hour_utc"].dt.tz is None:
        df["hour_utc"] = df["hour_utc"].dt.tz_localize("UTC")
    else:
        df["hour_utc"] = df["hour_utc"].dt.tz_convert("UTC")

    # Define eligibility based on coverage - PRIMARY EVALUATION: complete only
    eligible_primary = pd.Series(True, index=df.index)
    if "coverage_status" in df.columns:
        eligible_primary = df["coverage_status"].isin(config.primary_evaluation_eligible_statuses)
    if "is_dst_ambiguous_hour" in df.columns:
        eligible_primary = eligible_primary & ~df["is_dst_ambiguous_hour"].fillna(False)
    if "in_demand_mart" in df.columns:
        eligible_primary = eligible_primary & df["in_demand_mart"].fillna(True)

    # Sensitivity: include boundary/partial
    eligible_sensitivity = pd.Series(True, index=df.index)
    if "coverage_status" in df.columns:
        eligible_sensitivity = df["coverage_status"].isin(config.sensitivity_eligible_statuses)
    if "is_dst_ambiguous_hour" in df.columns:
        eligible_sensitivity = eligible_sensitivity & ~df["is_dst_ambiguous_hour"].fillna(False)
    if "in_demand_mart" in df.columns:
        eligible_sensitivity = eligible_sensitivity & df["in_demand_mart"].fillna(True)

    eligible = eligible_primary  # default for primary evaluation

    # Chronological splits
    train_mask, val_mask, test_mask = get_chronological_splits(df)
    print(f"Splits - train: {train_mask.sum()}, val: {val_mask.sum()}, test: {test_mask.sum()}")

    # Count by split
    def count_by_split(mask_eligible):
        return {
            "train": int((mask_eligible & train_mask).sum()),
            "val": int((mask_eligible & val_mask).sum()),
            "test": int((mask_eligible & test_mask).sum()),
            "total": int(mask_eligible.sum()),
        }

    results = {
        "contract": CONTRACT.to_dict(),
        "config": {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in config.__dict__.items()},
        "splits": {
            "train_n": int(train_mask.sum()),
            "val_n": int(val_mask.sum()),
            "test_n": int(test_mask.sum()),
            "train_start": str(df[train_mask]["hour_utc"].iloc[0]) if train_mask.sum() > 0 else None,
            "train_end": str(df[train_mask]["hour_utc"].iloc[-1]) if train_mask.sum() > 0 else None,
            "val_start": str(df[val_mask]["hour_utc"].iloc[0]) if val_mask.sum() > 0 else None,
            "val_end": str(df[val_mask]["hour_utc"].iloc[-1]) if val_mask.sum() > 0 else None,
            "test_start": str(df[test_mask]["hour_utc"].iloc[0]) if test_mask.sum() > 0 else None,
            "test_end": str(df[test_mask]["hour_utc"].iloc[-1]) if test_mask.sum() > 0 else None,
        },
        "eligibility": {
            "primary_evaluation_eligible_statuses": list(config.primary_evaluation_eligible_statuses),
            "sensitivity_eligible_statuses": list(config.sensitivity_eligible_statuses),
            "n_eligible_primary": int(eligible_primary.sum()),
            "n_eligible_sensitivity": int(eligible_sensitivity.sum()),
            "counts_by_split_primary": count_by_split(eligible_primary),
            "counts_by_split_sensitivity": count_by_split(eligible_sensitivity),
            "exclusions": df["coverage_status"].value_counts().to_dict() if "coverage_status" in df.columns else {},
            "total_spine_hours": int(len(df)),
        },
        "operational_availability": {
            "prediction_made_at": "beginning of target hour t",
            "lag1_availability": "explicitly optimistic baseline - ingestion latency unknown",
            "ingestion_latency_assumption": config.ingestion_latency_assumption_note,
            "previous_hour_available_at_prediction": config.previous_hour_available_at_prediction,
            "information_cutoff_hours": config.information_cutoff_hours,
            "conservative_no_lag1_configurable": True,
        },
    }

    baselines = [
        naive_previous_hour(df, require_lag1_available=True),  # optimistic
        seasonal_naive_lag24(df),
        seasonal_naive_lag168(df),
        historical_mean_past(df),
        naive_previous_hour_conservative(df),  # conservative no lag-1
    ]

    all_results_primary = {}
    for b in baselines:
        eval_res = evaluate_baseline(df, b, target_col=config.target_col, eligible_mask=eligible_primary)
        all_results_primary[b.method] = eval_res
        print(f"[PRIMARY] {b.method}: MAE={eval_res['mae']:.3f}, RMSE={eval_res['rmse']:.3f}, n={eval_res['n_evaluated']}")

    results["baselines_primary_evaluation"] = all_results_primary

    # Fair comparison on identical timestamps for all baselines (test set)
    b_prev = naive_previous_hour(df)
    b_lag24 = seasonal_naive_lag24(df)
    b_lag168 = seasonal_naive_lag168(df)
    b_hist = historical_mean_past(df)

    # Common mask for fair comparison: all must have predictions available and target available and eligible (primary)
    common_mask_test = (
        eligible_primary
        & test_mask
        & b_prev.mask_available
        & b_lag24.mask_available
        & b_lag168.mask_available
        & b_hist.mask_available
        & df[config.target_col].notna()
    )
    print(f"Common eligible timestamps for fair comparison (test, all baselines): {common_mask_test.sum()}")

    common_comparison = {}
    for b in [b_prev, b_lag24, b_lag168, b_hist]:
        m = common_mask_test  # identical for all
        y_true = df.loc[m, config.target_col].astype(float)
        y_pred = b.predictions.loc[m].astype(float)
        if len(y_true) > 0:
            mae = float(np.mean(np.abs(y_true - y_pred)))
            rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
            common_comparison[b.method] = {
                "mae": mae,
                "rmse": rmse,
                "n_evaluated": int(len(y_true)),
                "coverage_percentage": float(len(y_true) / max(1, int((eligible_primary & test_mask).sum())) * 100),
            }
        else:
            common_comparison[b.method] = {"mae": np.nan, "rmse": np.nan, "n_evaluated": 0}

    # Find strongest baseline
    best = min(common_comparison.items(), key=lambda x: x[1]["mae"] if not np.isnan(x[1]["mae"]) else float("inf"))
    results["fair_common_mask_comparison_test"] = {
        "common_mask_n": int(common_mask_test.sum()),
        "baselines": common_comparison,
        "strongest_baseline": best[0],
        "differences_against_strongest": {
            k: {
                "mae_diff": v["mae"] - best[1]["mae"] if not (np.isnan(v["mae"]) or np.isnan(best[1]["mae"])) else None,
                "rmse_diff": v["rmse"] - best[1]["rmse"] if not (np.isnan(v["rmse"]) or np.isnan(best[1]["rmse"])) else None,
            }
            for k, v in common_comparison.items()
        },
    }

    # Sensitivity analysis on boundary/partial
    sensitivity_results = {}
    for b in baselines[:4]:  # main baselines
        eval_res = evaluate_baseline(df, b, target_col=config.target_col, eligible_mask=eligible_sensitivity)
        sensitivity_results[b.method] = eval_res
    results["sensitivity_analysis_boundary_partial"] = sensitivity_results

    # Segment evaluation on test set (primary eligible)
    segment_results = {}
    for b in baselines[:4]:
        m = eligible_primary & test_mask & b.mask_available & df[config.target_col].notna()
        seg = evaluate_by_segment(df, b.predictions, df[config.target_col], m)
        segment_results[b.method] = seg
    results["segment_evaluation_test_primary"] = segment_results

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"Saved results to {out_path}")


if __name__ == "__main__":
    main()
