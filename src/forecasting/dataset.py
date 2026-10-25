from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import psycopg

from .config import ForecastingConfig


class ForecastDatasetBuilder:
    def __init__(self, config: Optional[ForecastingConfig] = None):
        self.config = config or ForecastingConfig()

    def fetch_hourly_demand_and_coverage(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Fetch from marts."""
        conn = psycopg.connect(self.config.database_url)
        try:
            demand = pd.read_sql("SELECT * FROM marts.fct_hourly_demand ORDER BY hour_utc", conn)
            coverage = pd.read_sql("SELECT * FROM marts.fct_hourly_coverage ORDER BY hour_utc", conn)
            return demand, coverage
        finally:
            conn.close()

    def build_continuous_spine_frame(self, demand: pd.DataFrame) -> pd.DataFrame:
        """Create continuous UTC hourly spine across the full range present in windows."""
        # Get min/max from demand/coverage coverage
        min_h = pd.to_datetime(demand["hour_utc"]).min()
        max_h = pd.to_datetime(demand["hour_utc"]).max()
        # Build full range
        full_range = pd.date_range(start=min_h, end=max_h, freq="h", tz="UTC")
        spine = pd.DataFrame({"hour_utc": full_range})
        return spine

    def merge_with_continuity(
        self,
        spine: pd.DataFrame,
        demand: pd.DataFrame,
        coverage: pd.DataFrame,
    ) -> pd.DataFrame:
        """Merge demand onto continuous spine; missing hours remain missing."""
        # Ensure tz-aware UTC
        for df in [spine, demand, coverage]:
            if "hour_utc" in df.columns:
                if not pd.api.types.is_datetime64_any_dtype(df["hour_utc"]):
                    df["hour_utc"] = pd.to_datetime(df["hour_utc"], utc=True)
                elif df["hour_utc"].dt.tz is None:
                    df["hour_utc"] = df["hour_utc"].dt.tz_localize("UTC")
                else:
                    df["hour_utc"] = df["hour_utc"].dt.tz_convert("UTC")

        merged = spine.merge(demand, on="hour_utc", how="left")
        cols_to_merge = ["hour_utc", "coverage_status", "coverage_reason", "in_demand_mart", "is_source_truncated"]
        # Add optional columns if they exist
        for c in ["in_outage_window", "is_dst_ambiguous_hour"]:
            if c in coverage.columns:
                cols_to_merge.append(c)
        merged = merged.merge(
            coverage[cols_to_merge],
            on="hour_utc",
            how="left",
        )
        return merged

    def save_dataset(self, df: pd.DataFrame, output_path: Path, config_path: Optional[Path] = None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Avoid committing large files - save as parquet if possible else csv
        try:
            df.to_parquet(output_path, index=False)
        except Exception:
            csv_path = output_path.with_suffix(".csv")
            df.to_csv(csv_path, index=False)
            print(f"Saved as CSV to {csv_path} (parquet engine unavailable)")
        if config_path:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                json.dump({"config": self.config.__dict__, "shape": list(df.shape)}, f, indent=2, default=str)
