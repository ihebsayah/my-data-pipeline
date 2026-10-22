from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from citi_bike_ingest.ingest.parser import ZipCSVParser


def _make_zip_with_files(contents: dict[str, str]) -> str:
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as z:
        for name, data in contents.items():
            z.writestr(name, data)
    return path


def test_duplicate_across_members() -> None:
    csv1 = """ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
X1,electric_bike,2026-01-01 00:00:00.000,2026-01-01 00:10:00.000,SA,1,SB,2,40.0,-74.0,40.1,-74.1,member
"""
    csv2 = """ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
X1,classic_bike,2026-01-02 00:00:00.000,2026-01-02 00:10:00.000,SA,1,SB,2,40.0,-74.0,40.1,-74.1,member
"""
    path = _make_zip_with_files({"202601_1.csv": csv1, "202601_2.csv": csv2})
    try:
        parser = ZipCSVParser(Path(path))
        batches = list(parser.iterate_rows(batch_size=10))
        rows = [r for b in batches for r in b]
        rejected = sum(1 for r in rows if r[0].get("_rejected") == "true")
        assert rejected == 0  # Database, not parser state, decides persistence conflicts.
    finally:
        Path(path).unlink()


def test_duplicate_across_month_archives() -> None:
    csv1 = """ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
M1,electric_bike,2026-01-01 00:00:00.000,2026-01-01 00:10:00.000,SA,1,SB,2,40.0,-74.0,40.1,-74.1,member
"""
    csv2 = """ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
M1,classic_bike,2026-02-01 00:00:00.000,2026-02-01 00:10:00.000,SA,1,SB,2,40.0,-74.0,40.1,-74.1,member
"""
    p1 = _make_zip_with_files({"202601.csv": csv1})
    p2 = _make_zip_with_files({"202602.csv": csv2})
    try:
        parser1 = ZipCSVParser(Path(p1))
        parser2 = ZipCSVParser(Path(p2))
        r1 = sum(1 for b in parser1.iterate_rows() for _ in b)
        r2 = sum(1 for b in parser2.iterate_rows() for _ in b)
        # Each parsed separately; dedup across months is enforced at DB level with UNIQUE(ride_id)
        assert r1 == 1 and r2 == 1
    finally:
        Path(p1).unlink()
        Path(p2).unlink()
