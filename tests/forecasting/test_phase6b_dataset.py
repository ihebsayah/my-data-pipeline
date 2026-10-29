import json
from pathlib import Path
import runpy
import sys

import pandas as pd

from forecasting.dataset import ForecastDatasetBuilder


def test_expanded_export_preserves_missing_edge_hours_and_t_minus_2(monkeypatch, tmp_path):
    hours = pd.date_range("2025-01-01 05:00", periods=8, freq="h", tz="UTC")
    demand = pd.DataFrame({"hour_utc": hours[1:-1], "rides_total": range(10, 16),
                           "is_source_truncated": False})
    coverage = pd.DataFrame({
        "hour_utc": hours,
        "coverage_status": ["uncertain"] + ["complete"] * 6 + ["uncertain"],
        "coverage_reason": ["unexplained_source_gap"] + [None] * 6 + ["unexplained_source_gap"],
        "in_demand_mart": [False] + [True] * 6 + [False],
        "is_source_truncated": False,
    })
    monkeypatch.setattr(ForecastDatasetBuilder, "fetch_hourly_demand_and_coverage",
                        lambda self: (demand, coverage))
    output = tmp_path / "expanded.csv"
    metadata_path = tmp_path / "expanded.metadata.json"
    monkeypatch.setattr(sys, "argv", ["phase6b_build_dataset.py", "--output", str(output),
                                      "--metadata-out", str(metadata_path)])
    script = Path(__file__).resolve().parents[2] / "scripts/phase6b_build_dataset.py"
    assert runpy.run_path(str(script))["main"]() == 0
    result = pd.read_csv(output)
    assert len(result) == 8
    assert result.loc[[0, 7], "rides_total"].isna().all()
    assert result.loc[3, "lag_2"] == 10
    assert result.loc[2, "lag_2"] != result.loc[2, "lag_2"]  # missing t-2 stays missing
    metadata = json.loads(metadata_path.read_text())
    assert metadata["contract"]["information_cutoff_hours"] == 2
    assert metadata["contract"]["previous_hour_observed_available_at_prediction"] is False
    assert "password" not in metadata_path.read_text().lower()
    assert metadata["trained"] is False and metadata["evaluated"] is False
