"""Phase 4C independent reconciliation.

Audits every ingested archive straight from the cached ZIPs (stdlib CSV only)
and compares the results against the live database — no ingestion or dbt
imports, so counts are produced independently of the code under test.

Usage:
    python scripts/phase4c_reconcile.py [--json out.json]
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
ARCHIVES = {
    "202510": "202510-citibike-tripdata.zip",
    "202511": "202511-citibike-tripdata.zip",
    "202512": "202512-citibike-tripdata.zip",
    "202601": "202601-citibike-tripdata.zip",
}


def psql(sql: str) -> list[list[str]]:
    cmd = [
        "docker", "exec", "citi_bike_postgres", "psql",
        "-U", "citi_bike", "-d", "citi_bike", "-t", "-A", "-F", "\t", "-c", sql,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [line.split("\t") for line in out.strip().splitlines() if line]


def audit_archive(month: str, filename: str) -> dict:
    path = CACHE / filename
    with open(path, "rb") as f:
        sha = hashlib.file_digest(f, "sha256").hexdigest()
    rows = 0
    with zipfile.ZipFile(path) as z:
        members = sorted(
            i.filename for i in z.infolist()
            if not i.is_dir() and i.filename.lower().endswith(".csv")
            and "__MACOSX" not in i.filename
            and not Path(i.filename).name.startswith(".")
        )
        for m in members:
            with z.open(m) as stream:
                rows += sum(1 for _ in csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig"))) - 1
    return {
        "month": month,
        "file_key": filename,
        "bytes": path.stat().st_size,
        "sha256": sha,
        "csv_members": len(members),
        "source_rows": rows,
    }


def main() -> int:
    report: dict = {"archives": [], "database": {}, "reconciliation": []}

    for month, filename in ARCHIVES.items():
        if not (CACHE / filename).exists():
            report["archives"].append({"month": month, "file_key": filename, "missing": True})
            continue
        report["archives"].append(audit_archive(month, filename))

    # Database-side counts, grouped by source archive key.
    db_counts = {
        row[0]: int(row[1])
        for row in psql(
            "select source_file_key, count(*) from raw.trips group by 1 order by 1;"
        )
    }
    db_manifest = {
        row[0]: {
            "checksum_sha256": row[1], "bytes": int(row[2]), "status": row[3],
        }
        for row in psql(
            "select month, checksum_sha256, bytes, status from ingest.manifest;"
        )
    }
    total_rows = int(psql("select count(*) from raw.trips;")[0][0])
    total_distinct = int(psql("select count(distinct ride_id) from raw.trips;")[0][0])
    stg_rows = int(psql("select count(*) from staging.stg_trips;")[0][0])
    stg_valid = int(
        psql("select count(*) from staging.stg_trips where started_at_status='valid';")[0][0]
    )
    stg_ambiguous = int(
        psql("select count(*) from staging.stg_trips where started_at_status='ambiguous';")[0][0]
    )
    demand_rows = int(psql("select count(*) from marts.fct_hourly_demand;")[0][0])
    demand_rides = int(psql("select sum(rides_total) from marts.fct_hourly_demand;")[0][0])
    coverage_rows = int(psql("select count(*) from marts.fct_hourly_coverage;")[0][0])
    coverage_status = psql(
        "select coverage_status, coverage_reason, count(*), sum(observed_trips), "
        "sum(demand_rides_total) from marts.fct_hourly_coverage group by 1,2 order by 1,2;"
    )

    report["database"] = {
        "raw_by_archive": db_counts,
        "raw_total": total_rows,
        "raw_distinct_ride_id": total_distinct,
        "cross_archive_duplicate_ride_ids": total_rows - total_distinct,
        "manifest": db_manifest,
        "staging_rows": stg_rows,
        "staging_valid_started": stg_valid,
        "staging_ambiguous_started": stg_ambiguous,
        "demand_rows": demand_rows,
        "demand_rides_total": demand_rides,
        "coverage_rows": coverage_rows,
        "coverage_by_status": coverage_status,
    }

    # Per-archive reconciliation: source rows vs DB rows vs checksum.
    for a in report["archives"]:
        if a.get("missing"):
            continue
        db = db_counts.get(a["file_key"], 0)
        man = db_manifest.get(a["month"], {})
        report["reconciliation"].append({
            "month": a["month"],
            "source_rows": a["source_rows"],
            "db_rows": db,
            "rows_match": a["source_rows"] == db,
            "checksum_match": a["sha256"] == man.get("checksum_sha256"),
            "bytes_match": a["bytes"] == man.get("bytes"),
            "manifest_status": man.get("status"),
        })

    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        Path(out).write_text(json.dumps(report, indent=2))
        print(f"wrote {out}")

    print(json.dumps(report, indent=2))
    ok = all(r["rows_match"] and r["checksum_match"] and r["bytes_match"]
             for r in report["reconciliation"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
