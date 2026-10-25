#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from forecasting.config import ForecastingConfig
from forecasting.contract import CONTRACT
from forecasting.dataset import ForecastDatasetBuilder
from forecasting.features import build_features


def main():
    parser = argparse.ArgumentParser(description="Build Phase 5A forecasting dataset")
    parser.add_argument("--output", default="data/processed/forecast_dataset.parquet")
    parser.add_argument("--config-out", default="config/forecasting_config.json")
    args = parser.parse_args()

    config = ForecastingConfig()
    builder = ForecastDatasetBuilder(config)

    print("Fetching demand and coverage from marts...")
    demand, coverage = builder.fetch_hourly_demand_and_coverage()
    print(f"Fetched demand shape: {demand.shape}, coverage shape: {coverage.shape}")

    print("Building continuous UTC hourly spine...")
    spine = builder.build_continuous_spine_frame(demand)
    print(f"Spine shape: {spine.shape}")

    print("Merging with continuity (missing hours remain missing)...")
    merged = builder.merge_with_continuity(spine, demand, coverage)

    print("Building leakage-safe features...")
    result = build_features(merged, target_col=config.target_col)
    df = result.df

    # Save
    out_path = Path(args.output)
    cfg_path = Path(args.config_out)
    builder.save_dataset(df, out_path, cfg_path)

    # Save metadata
    meta = {
        "contract": CONTRACT.to_dict(),
        "feature_metadata": result.metadata,
        "feature_columns": result.feature_columns,
        "shape": list(df.shape),
        "config": config.__dict__,
    }
    meta_path = out_path.with_suffix(".metadata.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)

    print(f"Saved dataset to {out_path}")
    print(f"Saved metadata to {meta_path}")
    print(f"Shape: {df.shape}")


if __name__ == "__main__":
    main()
