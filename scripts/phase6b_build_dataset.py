#!/usr/bin/env python3
"""Build the expanded Phase 6B forecasting dataset without training or evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from forecasting.config import ForecastingConfig
from forecasting.contract import CONTRACT
from forecasting.dataset import ForecastDatasetBuilder
from forecasting.features import (
    PRIMARY_FEATURE_COLUMNS,
    assert_primary_feature_contract,
    build_primary_features,
    validate_continuous_hourly_spine,
)


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="data/processed/phase6b/forecast_dataset_2025_01_2026_01.csv",
    )
    parser.add_argument(
        "--metadata-out",
        default="data/processed/phase6b/forecast_dataset_2025_01_2026_01.metadata.json",
    )
    args = parser.parse_args()

    config = ForecastingConfig(
        information_cutoff_hours=2,
        conservative_no_lag1=True,
        previous_hour_available_at_prediction=False,
    )
    builder = ForecastDatasetBuilder(config)
    demand, coverage = builder.fetch_hourly_demand_and_coverage()
    # Coverage defines the range even when its first/last hour has no demand.
    spine = builder.build_continuous_spine_frame(coverage)
    merged = builder.merge_with_continuity(spine, demand, coverage)
    result = build_primary_features(merged, target_col=config.target_col)
    df = result.df
    assert_primary_feature_contract(result.feature_columns)
    validate_continuous_hourly_spine(df)

    if df["hour_utc"].duplicated().any():
        raise ValueError("Expanded dataset contains duplicate hours")
    if df["rides_total"].isna().sum() != (~df["in_demand_mart"]).sum():
        raise ValueError("Target presence does not match coverage eligibility")
    if "lag_1" in df.columns:
        raise ValueError("Unsafe lag_1 column is present")
    if df.loc[~df["in_demand_mart"], "rides_total"].notna().any():
        raise ValueError("Unverified missing demand was filled")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)

    coverage_counts = {
        str(key): int(value)
        for key, value in df["coverage_status"].value_counts().sort_index().items()
    }
    metadata = {
        "contract": {
            **CONTRACT.to_dict(),
            "information_cutoff_hours": 2,
            "available_inputs": "calendar at t and observed demand no later than t-2",
            "previous_hour_observed_available_at_prediction": False,
            "ingestion_latency_assumption": (
                "Conservative t-2 availability; no lag-1 or shift(1) rolling features."
            ),
        },
        "phase": "6B",
        "trained": False,
        "evaluated": False,
        "dataset": {
            "path": str(output),
            "sha256": sha256(output),
            "rows": int(len(df)),
            "columns": int(len(df.columns)),
            "start_utc": str(df["hour_utc"].iloc[0]),
            "end_utc": str(df["hour_utc"].iloc[-1]),
            "unique_hours": int(df["hour_utc"].nunique()),
        },
        "coverage_counts": coverage_counts,
        "n_complete_targets": int(df["coverage_status"].eq("complete").sum()),
        "n_in_demand_mart": int(df["in_demand_mart"].sum()),
        "n_missing_targets": int(df["rides_total"].isna().sum()),
        "feature_columns": list(PRIMARY_FEATURE_COLUMNS),
        "feature_metadata": result.metadata,
        "checks": {
            "continuous_utc_hourly_spine": True,
            "unique_hours": True,
            "target_missing_iff_not_in_demand_mart": True,
            "no_zero_fill_of_unverified_demand": True,
            "lag_1_absent": "lag_1" not in df.columns,
            "no_unsafe_phase5a_rollups": not any(
                column.startswith("roll_") and not column.startswith("roll2_")
                for column in df.columns
            ),
            "primary_feature_contract": True,
        },
    }
    metadata_path = Path(args.metadata_out)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2, default=str) + "\n")

    print(json.dumps(metadata, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
