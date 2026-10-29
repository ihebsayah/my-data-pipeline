#!/usr/bin/env python3
"""Verify Phase 6B expansion integrity, coverage, DST, regression, and dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from citi_bike_ingest.config.settings import Settings
from forecasting.features import validate_continuous_hourly_spine

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKFILL = ROOT / "data" / "processed" / "phase6b" / "backfill.json"
DEFAULT_DATASET = (
    ROOT / "data" / "processed" / "phase6b" / "forecast_dataset_2025_01_2026_01.csv"
)
DEFAULT_METADATA = DEFAULT_DATASET.with_suffix(".metadata.json")
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "phase6b" / "verification.json"
BASELINE = ROOT / "data" / "processed" / "forecast_dataset.csv"
NEW_MONTHS = [f"2025{m:02d}" for m in range(1, 10)]


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def normalize_hour(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True)


def assert_series_equal(actual: pd.Series, expected: pd.Series, name: str) -> None:
    pd.testing.assert_series_equal(
        actual.reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False,
        check_dtype=False,
        obj=name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", default=str(DEFAULT_BACKFILL))
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA))
    parser.add_argument("--baseline", default=str(BASELINE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    report: dict = {"phase": "6B", "checks": {}, "metrics": {}}
    settings = Settings()
    dataset_path = Path(args.dataset)
    metadata = json.loads(Path(args.metadata).read_text())
    backfill = json.loads(Path(args.backfill).read_text())
    baseline = pd.read_csv(args.baseline)
    dataset = pd.read_csv(dataset_path)
    baseline["hour_utc"] = normalize_hour(baseline["hour_utc"])
    dataset["hour_utc"] = normalize_hour(dataset["hour_utc"])

    with psycopg.connect(settings.database_url) as conn:
        windows = pd.read_sql(
            "select month, coverage_start, coverage_end, window_label "
            "from staging.coverage_windows order by coverage_start",
            conn,
        )
        gaps = pd.read_sql(
            "select month, gap_start, gap_end, gap_label "
            "from staging.coverage_gaps order by gap_start",
            conn,
        )
        demand = pd.read_sql(
            "select * from marts.fct_hourly_demand order by hour_utc", conn
        )
        coverage = pd.read_sql(
            "select * from marts.fct_hourly_coverage order by hour_utc", conn
        )
        raw_by_file = pd.read_sql(
            "select source_file_key, count(*) as rows, "
            "count(distinct ride_id) as distinct_ride_ids, "
            "min(started_at_raw) as started_min, max(started_at_raw) as started_max "
            "from raw.trips group by 1 order by 1",
            conn,
        )
        rejects_by_file = pd.read_sql(
            "select source_file_key, count(*) as rows "
            "from raw.trips_rejects group by 1 order by 1",
            conn,
        )
        manifests = pd.read_sql(
            "select month, status, file_key, checksum_sha256, bytes, rows_read, "
            "rows_accepted, rows_rejected, duplicates_detected "
            "from ingest.manifest order by month",
            conn,
        )
        totals = conn.execute(
            "select count(*), count(distinct ride_id), "
            "(select count(*) from raw.trips_rejects), "
            "(select count(*) from staging.stg_trips) "
            "from raw.trips"
        ).fetchone()
        db_sizes = conn.execute(
            "select pg_database_size(current_database()), "
            "pg_total_relation_size('raw.trips'), "
            "pg_total_relation_size('staging.stg_trips')"
        ).fetchone()
        stg_relkind = conn.execute(
            "select c.relkind from pg_class c join pg_namespace n on n.oid=c.relnamespace "
            "where n.nspname='staging' and c.relname='stg_trips'"
        ).fetchone()[0]
        stg_indexes = [
            row[0]
            for row in conn.execute(
                "select indexdef from pg_indexes "
                "where schemaname='staging' and tablename='stg_trips' order by 1"
            ).fetchall()
        ]
        staging_statuses = conn.execute(
            "select started_at_status, count(*) from staging.stg_trips group by 1"
        ).fetchall()
        timestamp_errors = conn.execute(
            "select count(*) from staging.stg_trips where "
            "(started_at_status <> 'valid' and started_at_utc is not null) or "
            "(started_at_status = 'valid' and (started_at_utc is null or "
            "started_at_utc at time zone 'America/New_York' <> started_at_local))"
        ).fetchone()[0]
        old_fingerprint = conn.execute(
            "select count(*),sum(hashtextextended(row(ride_id,started_at_utc,"
            "ended_at_utc,started_at_status,ended_at_status)::text,0)::numeric)::text "
            "from staging.stg_trips where source_file_key >= '202510'"
        ).fetchone()

    windows["coverage_start"] = normalize_hour(windows["coverage_start"])
    windows["coverage_end"] = normalize_hour(windows["coverage_end"])
    gaps["gap_start"] = normalize_hour(gaps["gap_start"])
    gaps["gap_end"] = normalize_hour(gaps["gap_end"])
    demand["hour_utc"] = normalize_hour(demand["hour_utc"])
    coverage["hour_utc"] = normalize_hour(coverage["hour_utc"])

    expected_hours = int(
        (
            windows["coverage_end"] - windows["coverage_start"]
        ).dt.total_seconds().sum()
        // 3600
    )
    contiguous = bool(
        windows["coverage_end"].iloc[:-1].reset_index(drop=True).equals(
            windows["coverage_start"].iloc[1:].reset_index(drop=True)
        )
    )
    report["coverage"] = {
        "window_count": int(len(windows)),
        "first_start_utc": str(windows["coverage_start"].iloc[0]),
        "last_end_utc": str(windows["coverage_end"].iloc[-1]),
        "expected_hours": expected_hours,
        "coverage_rows": int(len(coverage)),
        "contiguous": contiguous,
        "gap_count": int(len(gaps)),
        "gaps": gaps.astype(str).to_dict(orient="records"),
    }
    report["checks"]["coverage_window_count"] = len(windows) == 13
    expected_boundaries = pd.date_range(
        "2025-01-01", "2026-02-01", freq="MS", tz="America/New_York"
    ).tz_convert("UTC")
    report["checks"]["timezone_correct_calendar_month_windows"] = bool(
        windows["coverage_start"].tolist() == expected_boundaries[:-1].tolist()
        and windows["coverage_end"].tolist() == expected_boundaries[1:].tolist()
    )
    report["checks"]["coverage_contiguous"] = contiguous
    report["checks"]["coverage_hours_match_spine"] = expected_hours == len(coverage)
    report["checks"]["january_outage_exact"] = bool(
        len(gaps) == 1
        and str(gaps["gap_start"].iloc[0]) == "2026-01-25 17:00:00+00:00"
        and str(gaps["gap_end"].iloc[0]) == "2026-01-26 12:00:00+00:00"
    )

    spring = coverage[
        (coverage["hour_utc"] >= pd.Timestamp("2025-03-09 05:00", tz="UTC"))
        & (coverage["hour_utc"] < pd.Timestamp("2025-03-10 04:00", tz="UTC"))
    ]
    fall = coverage[
        (coverage["hour_utc"] >= pd.Timestamp("2025-11-02 04:00", tz="UTC"))
        & (coverage["hour_utc"] < pd.Timestamp("2025-11-03 05:00", tz="UTC"))
    ]
    fall_uncertain = coverage[
        (coverage["hour_utc"].isin(
            [pd.Timestamp("2025-11-02 05:00", tz="UTC"),
             pd.Timestamp("2025-11-02 06:00", tz="UTC")]
        ))
    ]
    report["dst"] = {
        "spring_forward_local_day_hours": int(len(spring)),
        "fall_back_local_day_hours": int(len(fall)),
        "fall_back_uncertain_hours": int(len(fall_uncertain)),
        "fall_back_uncertain_statuses": fall_uncertain["coverage_status"].tolist(),
        "fall_back_uncertain_reasons": fall_uncertain["coverage_reason"].tolist(),
    }
    report["checks"]["spring_forward_day_23_hours"] = len(spring) == 23
    report["checks"]["fall_back_day_25_hours"] = len(fall) == 25
    report["checks"]["fall_back_two_uncertain_hours"] = bool(
        len(fall_uncertain) == 2
        and fall_uncertain["coverage_status"].eq("uncertain").all()
        and fall_uncertain["coverage_reason"].eq("dst_ambiguous_hour").all()
        and not fall_uncertain["in_demand_mart"].any()
    )

    coverage_counts = {
        str(key): int(value)
        for key, value in coverage["coverage_status"].value_counts().sort_index().items()
    }
    report["coverage"]["status_counts"] = coverage_counts
    report["checks"]["no_uncertain_in_demand_mart"] = not coverage.loc[
        coverage["coverage_status"].eq("uncertain"), "in_demand_mart"
    ].any()
    report["checks"]["no_unavailable_demand"] = bool(
        coverage.loc[
            coverage["coverage_status"].eq("unavailable"), "demand_rides_total"
        ].isna().all()
    )

    baseline_start = baseline["hour_utc"].min()
    baseline_end = baseline["hour_utc"].max()
    old_demand = demand[
        (demand["hour_utc"] >= baseline_start) & (demand["hour_utc"] <= baseline_end)
    ].reset_index(drop=True)
    old_coverage = coverage[
        (coverage["hour_utc"] >= baseline_start) & (coverage["hour_utc"] <= baseline_end)
    ].reset_index(drop=True)
    old = old_demand.merge(
        old_coverage,
        on="hour_utc",
        suffixes=("_demand", "_coverage"),
        how="right",
    )
    base = baseline.reset_index(drop=True)
    report["checks"]["old_period_row_count"] = len(old) == len(base) == 2953
    for new_col, old_col in (
        ("rides_total", "rides_total"),
        ("rides_member", "rides_member"),
        ("rides_casual", "rides_casual"),
        ("coverage_status", "coverage_status"),
        ("coverage_reason", "coverage_reason"),
        ("in_demand_mart", "in_demand_mart"),
        ("is_source_truncated_demand", "is_source_truncated_x"),
        ("is_source_truncated_coverage", "is_source_truncated_y"),
    ):
        assert_series_equal(old[new_col], base[old_col], new_col)
    report["checks"]["old_period_demand_and_coverage_unchanged"] = True

    dataset_start = dataset["hour_utc"].min()
    validate_continuous_hourly_spine(dataset)
    report["checks"]["dataset_continuous_hourly_spine"] = True
    dataset_end = dataset["hour_utc"].max()
    report["dataset"] = {
        "path": str(dataset_path),
        "sha256": sha256(dataset_path),
        "rows": int(len(dataset)),
        "columns": int(len(dataset.columns)),
        "unique_hours": int(dataset["hour_utc"].nunique()),
        "start_utc": str(dataset_start),
        "end_utc": str(dataset_end),
        "coverage_counts": {
            str(key): int(value)
            for key, value in dataset["coverage_status"].value_counts().sort_index().items()
        },
        "missing_targets": int(dataset["rides_total"].isna().sum()),
        "in_demand_rows": int(dataset["in_demand_mart"].sum()),
    }
    report["checks"]["dataset_rows_match_coverage"] = len(dataset) == len(coverage)
    mart_frame = coverage[["hour_utc", "coverage_status", "coverage_reason", "in_demand_mart"]].merge(
        demand[["hour_utc", "rides_total", "rides_member", "rides_casual"]],
        on="hour_utc", how="left", validate="one_to_one",
    ).sort_values("hour_utc").reset_index(drop=True)
    for column in mart_frame.columns:
        assert_series_equal(dataset[column], mart_frame[column], f"dataset mart {column}")
    report["checks"]["dataset_targets_and_eligibility_match_marts"] = True
    report["checks"]["dataset_unique_hours"] = len(dataset) == dataset["hour_utc"].nunique()
    report["checks"]["dataset_range_matches_coverage"] = (
        dataset_start == coverage["hour_utc"].min()
        and dataset_end == coverage["hour_utc"].max()
    )
    report["checks"]["dataset_hash_matches_metadata"] = (
        report["dataset"]["sha256"] == metadata["dataset"]["sha256"]
    )
    report["checks"]["dataset_lag_1_absent"] = "lag_1" not in dataset.columns
    report["checks"]["dataset_unsafe_rollups_absent"] = not any(
        column.startswith("roll_") and not column.startswith("roll2_")
        for column in dataset.columns
    )
    report["checks"]["target_missing_iff_not_eligible"] = bool(
        (
            dataset["rides_total"].isna()
            == ~dataset["in_demand_mart"].astype(bool)
        ).all()
    )
    report["checks"]["no_unverified_target_fill"] = bool(
        dataset.loc[~dataset["in_demand_mart"].astype(bool), "rides_total"].isna().all()
    )

    lag_2 = dataset["rides_total"].shift(2)
    lag_24 = dataset["rides_total"].shift(24)
    lag_168 = dataset["rides_total"].shift(168)
    report["checks"]["lag_2_matches_t_minus_2"] = bool(
        np.allclose(
            dataset["lag_2"].to_numpy(dtype=float),
            lag_2.to_numpy(dtype=float),
            equal_nan=True,
        )
    )
    report["checks"]["lag_24_exact"] = bool(
        np.allclose(
            dataset["lag_24"].to_numpy(dtype=float),
            lag_24.to_numpy(dtype=float),
            equal_nan=True,
        )
    )
    report["checks"]["lag_168_exact"] = bool(
        np.allclose(
            dataset["lag_168"].to_numpy(dtype=float),
            lag_168.to_numpy(dtype=float),
            equal_nan=True,
        )
    )
    roll2_mean_24 = dataset["rides_total"].shift(2).rolling(24, min_periods=24).mean()
    report["checks"]["roll2_mean_24_exact"] = bool(
        np.allclose(
            dataset["roll2_mean_24"].to_numpy(dtype=float),
            roll2_mean_24.to_numpy(dtype=float),
            equal_nan=True,
        )
    )

    raw_total, raw_distinct, reject_total, staged_total = totals
    report["database"] = {
        "raw_rows": int(raw_total),
        "raw_distinct_ride_ids": int(raw_distinct),
        "staging_rows": int(staged_total),
        "reject_rows": int(reject_total),
        "database_bytes": int(db_sizes[0]),
        "raw_trips_bytes": int(db_sizes[1]),
        "stg_trips_bytes": int(db_sizes[2]),
        "staging_relkind": stg_relkind,
        "staging_indexes": stg_indexes,
        "files": raw_by_file.astype(str).to_dict(orient="records"),
        "rejects": rejects_by_file.astype(str).to_dict(orient="records"),
        "manifest": manifests.astype(str).to_dict(orient="records"),
    }
    report["checks"]["cross_archive_duplicate_ids_zero"] = raw_total == raw_distinct
    report["checks"]["staging_rows_equal_raw_rows"] = staged_total == raw_total
    report["checks"]["staging_is_table"] = stg_relkind == "r"
    report["checks"]["staging_has_three_indexes"] = len(stg_indexes) == 3
    report["database"]["staging_statuses"] = dict(staging_statuses)
    report["checks"]["no_invented_or_invalid_start_utc"] = timestamp_errors == 0
    preflight = json.loads(
        (ROOT / "data/processed/phase6b/preflight-resumption.json").read_text()
    )
    report["checks"]["existing_period_timestamp_fingerprint_unchanged"] = (
        list(old_fingerprint) == preflight["old_timestamp_fingerprint"]
    )
    expected_file_keys = {
        f"{year}{month:02d}-citibike-tripdata.zip"
        for year, months in ((2025, range(1, 13)), (2026, range(1, 2)))
        for month in months
    }
    report["checks"]["all_expected_source_file_keys"] = set(
        raw_by_file["source_file_key"]
    ) == expected_file_keys
    report["checks"]["no_synthetic_test_rows"] = not raw_by_file[
        "source_file_key"
    ].astype(str).str.contains("test").any()

    for month in NEW_MONTHS:
        audit = backfill["archives"][month]
        raw_rows = int(
            raw_by_file.loc[
                raw_by_file["source_file_key"].eq(audit["file_key"]), "rows"
            ].iloc[0]
        )
        report["checks"][f"{month}_source_reconciled"] = (
            raw_rows == audit["accepted_unique_ride_ids"]
        )
        reconciliation = backfill["reconciliation"].get(month, {})
        report["checks"][f"{month}_manifest_reconciled"] = bool(
            reconciliation.get("checks")
            and all(reconciliation["checks"].values())
        )

    for lag in (2, 24, 48, 72, 168):
        assert_series_equal(dataset[f"lag_{lag}"], dataset["rides_total"].shift(lag),
                            f"lag_{lag}")
    for window in (24, 168):
        rolling = dataset["rides_total"].shift(2).rolling(window, min_periods=window)
        for stat in ("mean", "std", "min", "max"):
            assert_series_equal(dataset[f"roll2_{stat}_{window}"],
                                getattr(rolling, stat)(), f"roll2_{stat}_{window}")
    report["checks"]["all_history_features_obey_t_minus_2"] = True
    preserved = json.loads(
        (ROOT / "data/processed/phase6a/frozen_artifact_preservation.json").read_text()
    )["after"]
    report["checks"]["frozen_phase5b_artifacts_unchanged"] = all(
        sha256(ROOT / "data/processed/phase5b" / name) == info["sha256"]
        for name, info in preserved.items()
    )
    report["checks"]["frozen_phase5b_dataset_unchanged"] = sha256(Path(args.baseline)) == (
        "bbabaf23211482321edb75051ea111df121209e62763adc4c72d838c1c1aafee"
    )

    report["all_checks_passed"] = all(report["checks"].values())
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
