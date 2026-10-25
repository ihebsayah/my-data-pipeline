"""Audit and ingest the Jan-Sep 2025 Citi Bike archives one month at a time.

The audit is independent of the ingestion parser: it streams each ZIP with the
standard library, accounts for rows that the parser will reject, and counts
duplicate ride_ids before any database write. Ingestion uses the existing
reconciled loader, then every manifest/source/database invariant is checked
before the next month starts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from citi_bike_ingest.config.settings import Settings
from citi_bike_ingest.ingest.downloader import Downloader
from citi_bike_ingest.ingest.orchestrator import IngestionOrchestrator
from citi_bike_ingest.ingest.s3_listing import CitiBikeS3Listing

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MONTHS = [f"2025{m:02d}" for m in range(1, 10)]
REPORT_PATH = ROOT / "data" / "processed" / "phase6b" / "backfill.json"
REQUIRED_COLUMNS = {
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "start_station_name",
    "start_station_id",
    "end_station_name",
    "end_station_id",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
    "member_casual",
}


class ReconciliationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit_archive(path: Path) -> dict:
    started = time.time()
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            info.filename
            for info in archive.infolist()
            if not info.is_dir()
            and info.filename.lower().endswith(".csv")
            and "__MACOSX" not in info.filename
            and not Path(info.filename).name.startswith(".")
        )
        if not members:
            raise ReconciliationError(f"No CSV members in {path}")

        seen: set[str] = set()
        per_member: dict[str, int] = {}
        rows = eligible = rejected = duplicates = 0
        missing_ride_id = missing_started_at = missing_ended_at = 0
        started_min = started_max = None

        for member in members:
            member_rows = 0
            with archive.open(member) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames or [])
                if missing_columns:
                    raise ReconciliationError(
                        f"Invalid header in {member}: missing {sorted(missing_columns)}"
                    )
                for row in reader:
                    member_rows += 1
                    rows += 1
                    ride_id = (row.get("ride_id") or "").strip()
                    started_at = (row.get("started_at") or "").strip()
                    ended_at = (row.get("ended_at") or "").strip()
                    if not ride_id:
                        missing_ride_id += 1
                        rejected += 1
                        continue
                    if not started_at:
                        missing_started_at += 1
                        rejected += 1
                        continue
                    if not ended_at:
                        missing_ended_at += 1
                        rejected += 1
                        continue
                    eligible += 1
                    if ride_id in seen:
                        duplicates += 1
                    else:
                        seen.add(ride_id)
                    if started_min is None or started_at < started_min:
                        started_min = started_at
                    if started_max is None or started_at > started_max:
                        started_max = started_at
            per_member[member] = member_rows

    result = {
        "archive": str(path),
        "file_key": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "csv_members": members,
        "rows_by_member": per_member,
        "rows": rows,
        "eligible_rows": eligible,
        "accepted_unique_ride_ids": len(seen),
        "duplicate_ride_ids": duplicates,
        "rejected_rows": rejected,
        "rejected_missing_ride_id": missing_ride_id,
        "rejected_missing_started_at": missing_started_at,
        "rejected_missing_ended_at": missing_ended_at,
        "accepted_started_at_min": started_min,
        "accepted_started_at_max": started_max,
        "audit_seconds": round(time.time() - started, 2),
    }
    if rows != eligible + rejected or eligible != len(seen) + duplicates:
        raise ReconciliationError(f"Source audit accounting failed for {path.name}")
    return result


def manifest_row(conn: psycopg.Connection, month: str) -> dict | None:
    row = conn.execute(
        """
        select id, status, file_key, checksum_sha256, bytes, rows_read,
               rows_accepted, rows_rejected, duplicates_detected, error_message
        from ingest.manifest
        where month = %s
        order by id desc
        limit 1
        """,
        (month,),
    ).fetchone()
    if row is None:
        return None
    keys = (
        "id", "status", "file_key", "checksum_sha256", "bytes", "rows_read",
        "rows_accepted", "rows_rejected", "duplicates_detected", "error_message",
    )
    return dict(zip(keys, row))


def raw_month_stats(conn: psycopg.Connection, file_key: str) -> dict:
    row = conn.execute(
        """
        select count(*), count(distinct ride_id), min(started_at_raw),
               max(started_at_raw), min(_ingested_at), max(_ingested_at)
        from raw.trips
        where source_file_key = %s
        """,
        (file_key,),
    ).fetchone()
    return {
        "rows": row[0],
        "distinct_ride_ids": row[1],
        "started_at_raw_min": row[2],
        "started_at_raw_max": row[3],
        "ingested_at_min": row[4],
        "ingested_at_max": row[5],
    }


def database_totals(conn: psycopg.Connection) -> dict:
    row = conn.execute(
        """
        select count(*), count(distinct ride_id),
               (select count(*) from raw.trips_rejects)
        from raw.trips
        """
    ).fetchone()
    return {
        "raw_rows": row[0],
        "raw_distinct_ride_ids": row[1],
        "raw_duplicate_ride_ids": row[0] - row[1],
        "reject_rows": row[2],
        "database_bytes": conn.execute(
            "select pg_database_size(current_database())"
        ).fetchone()[0],
        "raw_trips_bytes": conn.execute(
            "select pg_total_relation_size('raw.trips')"
        ).fetchone()[0],
        "stg_trips_bytes": conn.execute(
            "select pg_total_relation_size('staging.stg_trips')"
        ).fetchone()[0],
    }


def verify_month(conn: psycopg.Connection, month: str, audit: dict) -> dict:
    manifest = manifest_row(conn, month)
    if manifest is None:
        raise ReconciliationError(f"No manifest for {month}")
    raw = raw_month_stats(conn, audit["file_key"])
    checks = {
        "manifest_completed": manifest["status"] == "completed",
        "file_key_match": manifest["file_key"] == audit["file_key"],
        "checksum_match": manifest["checksum_sha256"] == audit["sha256"],
        "bytes_match": manifest["bytes"] == audit["bytes"],
        "rows_read_match": manifest["rows_read"] == audit["rows"],
        "row_accounting": (
            manifest["rows_accepted"]
            + manifest["rows_rejected"]
            + manifest["duplicates_detected"]
            == manifest["rows_read"]
        ),
        "rejects_match": manifest["rows_rejected"] == audit["rejected_rows"],
        "raw_count_match": raw["rows"] == audit["accepted_unique_ride_ids"],
        "raw_ids_unique": raw["rows"] == raw["distinct_ride_ids"],
        "raw_started_range_match": (
            raw["started_at_raw_min"] == audit["accepted_started_at_min"]
            and raw["started_at_raw_max"] == audit["accepted_started_at_max"]
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise ReconciliationError(
            f"{month} reconciliation failed: {failures}; "
            f"manifest={manifest}; raw={raw}; audit={audit}"
        )
    return {
        "month": month,
        "checks": checks,
        "manifest": manifest,
        "raw": raw,
        "database": database_totals(conn),
        "host_free_bytes": shutil.disk_usage(ROOT).free,
    }


def write_report(report: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, default=str) + "\n")
    tmp.replace(REPORT_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", default=",".join(DEFAULT_MONTHS))
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    months = [month.strip() for month in args.months.split(",") if month.strip()]

    settings = Settings()
    cache_dir = ROOT / settings.cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    listing = CitiBikeS3Listing()
    objects = listing.fetch_listing()
    downloader = Downloader(
        cache_dir, settings.retry_attempts, settings.retry_delay
    )
    orchestrator = IngestionOrchestrator(settings)
    report = {
        "phase": "6B",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "months": months,
        "archives": {},
        "ingestion": {},
        "reconciliation": {},
    }
    if REPORT_PATH.exists():
        previous = json.loads(REPORT_PATH.read_text())
        if previous.get("months") == months:
            report = previous
            report["started_at_utc"] = datetime.now(timezone.utc).isoformat()

    with psycopg.connect(settings.database_url) as conn:
        for month in months:
            print(f"[{month}] fetching source object", flush=True)
            obj = listing.find_nyc_month(month, objects)
            if obj is None:
                raise ReconciliationError(f"No NYC archive listed for {month}")
            path = downloader.download(
                obj.key, f"{listing.BUCKET_URL}{obj.key}", obj.size
            )
            if path.stat().st_size != obj.size:
                raise ReconciliationError(
                    f"{month} size mismatch: {path.stat().st_size} != {obj.size}"
                )

            print(f"[{month}] auditing {path.name}", flush=True)
            audit = audit_archive(path)
            report["archives"][month] = audit
            write_report(report)
            print(
                f"[{month}] rows={audit['rows']} unique={audit['accepted_unique_ride_ids']} "
                f"dups={audit['duplicate_ride_ids']} rejects={audit['rejected_rows']} "
                f"seconds={audit['audit_seconds']}",
                flush=True,
            )

            current = manifest_row(conn, month)
            if current and current["status"] == "completed":
                if current["checksum_sha256"] != audit["sha256"]:
                    raise ReconciliationError(
                        f"{month} completed manifest has a different source checksum"
                    )
                report["reconciliation"][month] = verify_month(conn, month, audit)
                write_report(report)
                print(f"[{month}] already complete and reconciled; skipped", flush=True)
                continue
            if args.audit_only:
                continue

            print(f"[{month}] ingesting to raw.trips", flush=True)
            before = database_totals(conn)
            ingested = orchestrator.ingest_archive(
                path, obj.key, month, expected_rows=audit["rows"], use_bulk=True
            )
            report["ingestion"][month] = {
                **ingested,
                "database_before": before,
                "database_after": database_totals(conn),
                "host_free_bytes_after": shutil.disk_usage(ROOT).free,
            }
            write_report(report)
            verification = verify_month(conn, month, audit)
            report["reconciliation"][month] = verification
            write_report(report)
            print(
                f"[{month}] reconciled: accepted={ingested['rows_accepted']} "
                f"dups={ingested['duplicates_detected']} "
                f"rejected={ingested['rows_rejected']} "
                f"elapsed={ingested['elapsed_seconds']}s",
                flush=True,
            )

        if not args.audit_only:
            totals = database_totals(conn)
            if totals["raw_duplicate_ride_ids"] != 0:
                raise ReconciliationError(
                    f"Cross-archive duplicate ride_ids: {totals['raw_duplicate_ride_ids']}"
                )
            report["final"] = {
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "database": totals,
                "host_free_bytes": shutil.disk_usage(ROOT).free,
                "all_months_reconciled": all(
                    month in report["reconciliation"] for month in months
                ),
            }
            write_report(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
