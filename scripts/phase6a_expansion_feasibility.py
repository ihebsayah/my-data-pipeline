from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from citi_bike_ingest.ingest.s3_listing import CitiBikeS3Listing

MIB = 1024**2
GIB = 1024**3

MEASURED_MONTHS = {
    "202510": {"archive_bytes": 924_115_389, "rows": 4_731_900, "ingest_minutes": 43.9},
    "202511": {"archive_bytes": 667_113_932, "rows": 3_417_052, "ingest_minutes": 35.5},
    "202512": {"archive_bytes": 407_790_791, "rows": 2_091_620, "ingest_minutes": 23.6},
    "202601": {"archive_bytes": 353_829_139, "rows": 1_816_391, "ingest_minutes": 11.0},
}
MEASURED_TOTAL_ROWS = 12_056_963
MEASURED_RAW_BYTES = 5_045 * MIB
MEASURED_STG_BYTES = 4_743 * MIB
MEASURED_DB_BYTES = 9_797 * MIB
MEASURED_DBT_FULL_REFRESH_SECONDS = 39 * 60 + 31

CANDIDATES = {
    "six_new_months_2025_04_to_2025_09": [
        "202504", "202505", "202506", "202507", "202508", "202509",
    ],
    "recommended_nine_new_months_2025_01_to_2025_09": [
        "202501", "202502", "202503", "202504", "202505",
        "202506", "202507", "202508", "202509",
    ],
    "twelve_new_months_2024_10_to_2025_09": [
        "202410", "202411", "202412", "202501", "202502", "202503",
        "202504", "202505", "202506", "202507", "202508", "202509",
    ],
}


def s3_sizes() -> dict[str, dict[str, Any]]:
    listing = CitiBikeS3Listing(timeout=60)
    objects = listing.fetch_listing()
    sizes: dict[str, dict[str, Any]] = {}
    wanted = sorted({month for months in CANDIDATES.values() for month in months})
    for month in wanted:
        obj = listing.find_nyc_month(month, objects)
        sizes[month] = {
            "available": obj is not None,
            "key": obj.key if obj else None,
            "bytes": obj.size if obj else None,
            "last_modified": obj.last_modified if obj else None,
        }
    return {
        "queried_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": CitiBikeS3Listing.BUCKET_URL,
        "n_objects_listed": len(objects),
        "months": sizes,
    }


def estimate_window(
    months: list[str],
    s3: dict[str, dict[str, Any]],
    bytes_per_row: float,
    db_bytes_per_row: float,
    free_bytes: int,
) -> dict[str, Any]:
    missing = [month for month in months if not s3["months"][month]["available"]]
    if missing:
        raise ValueError(f"Candidate window has unavailable S3 months: {missing}")

    archive_bytes = sum(int(s3["months"][m]["bytes"]) for m in months)
    rows = archive_bytes / bytes_per_row
    additional_db_bytes = rows * db_bytes_per_row
    cache_bytes = archive_bytes
    required_free_bytes = (additional_db_bytes + cache_bytes) * 1.25

    ingestion = {}
    for throughput in (1500, 1800):
        hours = rows / throughput / 3600
        ingestion[str(throughput)] = {
            "rows_per_second": throughput,
            "hours": hours,
            "hours_with_25pct_buffer": hours * 1.25,
        }

    projected_total_rows = MEASURED_TOTAL_ROWS + rows
    dbt_seconds = (
        MEASURED_DBT_FULL_REFRESH_SECONDS
        * projected_total_rows
        / MEASURED_TOTAL_ROWS
    )
    dbt_hours = dbt_seconds / 3600

    return {
        "new_months": months,
        "n_new_archives": len(months),
        "new_archive_bytes": int(archive_bytes),
        "new_archive_gib": archive_bytes / GIB,
        "estimated_new_rows": int(round(rows)),
        "existing_months": 4,
        "total_months_after_expansion": len(months) + 4,
        "additional_db_bytes": int(round(additional_db_bytes)),
        "additional_db_gib": additional_db_bytes / GIB,
        "projected_total_db_gib": (MEASURED_DB_BYTES + additional_db_bytes) / GIB,
        "cache_gib": cache_bytes / GIB,
        "required_free_gib_with_25pct_buffer": required_free_bytes / GIB,
        "available_free_gib": free_bytes / GIB,
        "fits_current_free_space": required_free_bytes <= free_bytes,
        "ingestion_estimate": ingestion,
        "dbt_full_refresh_estimate": {
            "projected_total_rows": int(round(projected_total_rows)),
            "hours": dbt_hours,
            "hours_with_25pct_buffer": dbt_hours * 1.25,
            "assumption": "Linear scaling of measured 39m31s full refresh.",
        },
        "total_processing_hours_at_1500_rows_s": (
            ingestion["1500"]["hours"] + dbt_hours
        ),
        "total_processing_hours_at_1500_rows_s_with_25pct_buffer": (
            ingestion["1500"]["hours_with_25pct_buffer"]
            + dbt_hours * 1.25
        ),
    }


def build_payload(output_path: Path | None = None) -> dict[str, Any]:
    s3 = s3_sizes()
    total_archive_bytes = sum(v["archive_bytes"] for v in MEASURED_MONTHS.values())
    bytes_per_row = total_archive_bytes / MEASURED_TOTAL_ROWS
    db_bytes_per_row = (
        MEASURED_RAW_BYTES + MEASURED_STG_BYTES
    ) / MEASURED_TOTAL_ROWS
    free_bytes = shutil.disk_usage(".").free

    candidates = {
        name: estimate_window(
            months, s3, bytes_per_row, db_bytes_per_row, free_bytes
        )
        for name, months in CANDIDATES.items()
    }

    recommended = candidates["recommended_nine_new_months_2025_01_to_2025_09"]
    payload = {
        "phase": "6A",
        "purpose": (
            "Feasibility of expanding historical demand by 6-12 months using "
            "measured Phase 4C ingestion, dbt, disk, and S3 archive data."
        ),
        "s3": s3,
        "measured_baseline": {
            "months": MEASURED_MONTHS,
            "total_rows": MEASURED_TOTAL_ROWS,
            "total_archive_bytes": total_archive_bytes,
            "archive_bytes_per_row": bytes_per_row,
            "raw_trips_bytes": MEASURED_RAW_BYTES,
            "stg_trips_bytes": MEASURED_STG_BYTES,
            "database_bytes": MEASURED_DB_BYTES,
            "db_bytes_per_row": db_bytes_per_row,
            "dbt_full_refresh_seconds": MEASURED_DBT_FULL_REFRESH_SECONDS,
            "host_free_bytes": free_bytes,
        },
        "candidates": candidates,
        "recommendation": {
            "window": "2025-01-01 through 2025-09-30",
            "candidate_key": "recommended_nine_new_months_2025_01_to_2025_09",
            "total_months_after_expansion": recommended[
                "total_months_after_expansion"
            ],
            "rationale": [
                "Nine new consecutive archives turn the existing four months into a 13-month span.",
                "The span covers winter, spring, and summer seasonality, improving seasonal robustness.",
                "All nine S3 archives are currently listed and downloadable.",
                "The projected disk and processing cost fit the measured host capacity.",
                "No new pipeline architecture is required: add seed windows, ingest monthly, then rebuild dbt.",
            ],
            "estimated_new_rows": recommended["estimated_new_rows"],
            "estimated_additional_db_gib": recommended["additional_db_gib"],
            "estimated_cache_gib": recommended["cache_gib"],
            "estimated_required_free_gib_with_buffer": recommended[
                "required_free_gib_with_25pct_buffer"
            ],
            "estimated_ingestion_hours_1500_rows_s_with_buffer": recommended[
                "ingestion_estimate"
            ]["1500"]["hours_with_25pct_buffer"],
            "estimated_dbt_hours_with_buffer": recommended[
                "dbt_full_refresh_estimate"
            ]["hours_with_25pct_buffer"],
        },
        "risks_and_notes": [
            "Estimates assume archive bytes per row observed in the four measured months.",
            "dbt full-refresh time is scaled linearly; incremental staging should be considered for 9+ new months.",
            "Do not zero-fill unverified demand. Missing/unavailable hours must remain NaN.",
            "Add DST-correct seed boundaries and rerun coverage assertions for every new month.",
            "Ingestion and dbt should be run one month at a time with reconciliation after each archive.",
        ],
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/phase6a/expansion_feasibility.json"),
    )
    args = parser.parse_args()
    payload = build_payload(args.output)
    print(json.dumps(payload["recommendation"], indent=2))


if __name__ == "__main__":
    main()
