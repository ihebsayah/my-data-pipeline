"""Controlled incident verification. Existing January rows are never removed."""
import csv
import io
import json
import os
import sqlite3
import sys
import uuid
import zipfile
from itertools import islice
from pathlib import Path

import psycopg
from psycopg import sql

from citi_bike_ingest.config.settings import Settings
from citi_bike_ingest.ingest.orchestrator import IngestionOrchestrator
from snapshot_database import snapshot

EVIDENCE = Path("docs/evidence/phase-3.4")
ARCHIVE = Path("data/cache/202601-citibike-tripdata.zip")


def membership(label):
    """Compare complete database/source ID sets using disk-backed joins."""
    disk = sqlite3.connect(os.environ.get("CITI_AUDIT_DB", "/tmp/citi-january-audit.sqlite"))
    disk.execute("CREATE TABLE IF NOT EXISTS persisted (ride_id TEXT PRIMARY KEY)")
    disk.execute("DELETE FROM persisted")
    with psycopg.connect(Settings().database_url) as conn:
        with conn.cursor(name="all_ids") as cur:
            cur.execute("SELECT ride_id FROM raw.trips")
            while rows := cur.fetchmany(10000):
                disk.executemany("INSERT INTO persisted VALUES (?)", rows)
                disk.commit()
        temporal = conn.execute("""
            SELECT _ingested_at, count(*), min(id), max(id)
            FROM raw.trips GROUP BY 1 ORDER BY 1
        """).fetchall()
    result = {
        "missing": disk.execute(
            "SELECT count(*) FROM ids LEFT JOIN persisted USING(ride_id) "
            "WHERE persisted.ride_id IS NULL").fetchone()[0],
        "extra": disk.execute(
            "SELECT count(*) FROM persisted LEFT JOIN ids USING(ride_id) "
            "WHERE ids.ride_id IS NULL").fetchone()[0],
        "ingestion_transactions": temporal,
    }
    if label == "before":
        # Test whether the original DB is exactly a prefix of the first CSV.
        with zipfile.ZipFile(ARCHIVE) as z:
            with z.open("202601-citibike-tripdata_1.csv") as stream:
                reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8"))
                prefix_present = sum(
                    disk.execute("SELECT 1 FROM persisted WHERE ride_id=?",
                                 (r["ride_id"],)).fetchone() is not None
                    for r in islice(reader, 330000)
                )
        result["first_330000_source_rows_present"] = prefix_present
    disk.close()
    (EVIDENCE / f"{label}-membership.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, indent=2, default=str))
    return result


def bounded():
    path = EVIDENCE / "bounded.zip"
    with zipfile.ZipFile(ARCHIVE) as source, zipfile.ZipFile(path, "w") as dest:
        for name in sorted(source.namelist()):
            with source.open(name) as stream:
                reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8"))
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(next(reader))
                writer.writerows(islice(reader, 20003))
                dest.writestr(name, output.getvalue())
    base = Settings()
    dbname = "citi_bounded_" + uuid.uuid4().hex
    with psycopg.connect(base.database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    settings = base.model_copy(update={"postgres_db": dbname, "batch_size": 10000})
    runner = IngestionOrchestrator(settings)
    first = runner.ingest_archive(path, path.name, "202601", 40006)
    assert (first["rows_accepted"], first["duplicates_detected"], first["rows_rejected"]) == (
        40006, 0, 0)
    second = runner.ingest_archive(path, path.name, "202601", 40006)
    assert (second["rows_accepted"], second["duplicates_detected"], second["rows_rejected"]) == (
        0, 40006, 0)
    with psycopg.connect(settings.database_url) as conn:
        count = conn.execute("SELECT count(*), count(DISTINCT ride_id) FROM raw.trips").fetchone()
        assert count == (40006, 40006)
    (EVIDENCE / "bounded.json").write_text(json.dumps(
        dict(first=first, rerun=second, database_counts=count), indent=2))
    with psycopg.connect(base.database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(dbname)))


def full(label):
    expected = json.loads((EVIDENCE / "source.json").read_text())
    runner = IngestionOrchestrator(Settings(batch_size=10000))
    result = runner.ingest_archive(ARCHIVE, ARCHIVE.name, "202601", expected["rows"])
    assert result["checksum_sha256"] == expected["sha256"]
    (EVIDENCE / f"{label}.json").write_text(json.dumps(result, indent=2))
    snapshot(EVIDENCE / f"{label}-database.json")
    ids = membership(label)
    assert ids["missing"] == ids["extra"] == 0
    if label == "rerun":
        assert result["rows_accepted"] == result["rows_rejected"] == 0
        assert result["duplicates_detected"] == expected["rows"]


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "before":
        membership(mode)
    elif mode == "bounded":
        bounded()
    elif mode in ("full", "rerun"):
        full(mode)
    else:
        raise ValueError(mode)
