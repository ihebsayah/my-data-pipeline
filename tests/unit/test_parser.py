from __future__ import annotations

import io
import zipfile
from pathlib import Path
import tempfile
import os

from citi_bike_ingest.ingest.parser import ZipCSVParser


def create_test_zip(csv_content: str, filename: str = "test.csv") -> Path:
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(filename, csv_content)
    return Path(path)


def test_parse_basic() -> None:
    csv = """ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
123,electric_bike,2026-01-01 00:00:00.000,2026-01-01 00:10:00.000,StationA,SA1,StationB,SB1,40.0,-74.0,40.1,-74.1,member
"""
    zpath = create_test_zip(csv)
    try:
        parser = ZipCSVParser(zpath)
        members = parser.get_csv_members()
        assert members == ["test.csv"]
        batches = list(parser.iterate_rows(batch_size=10))
        assert len(batches) == 1
        assert len(batches[0]) == 1  # 1 accepted row
    finally:
        zpath.unlink(missing_ok=True)


def test_parse_multiple_members() -> None:
    csv1 = """ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
1,electric_bike,2026-01-01 00:00:00.000,2026-01-01 00:10:00.000,SA,1,SB,2,40.0,-74.0,40.1,-74.1,member
"""
    csv2 = """ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
2,classic_bike,2026-01-01 01:00:00.000,2026-01-01 01:10:00.000,SC,3,SD,4,40.0,-74.0,40.1,-74.1,casual
"""
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("202601_1.csv", csv1)
        z.writestr("202601_2.csv", csv2)
    try:
        parser = ZipCSVParser(Path(path))
        members = parser.get_csv_members()
        assert len(members) == 2
        batches = list(parser.iterate_rows(batch_size=5))
        # Both rows accepted
        total = sum(len(b) for b in batches)
        assert total == 2
    finally:
        Path(path).unlink(missing_ok=True)


def test_duplicate_ride_id() -> None:
    csv = """ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
DUP,electric_bike,2026-01-01 00:00:00.000,2026-01-01 00:10:00.000,SA,1,SB,2,40.0,-74.0,40.1,-74.1,member
DUP,classic_bike,2026-01-01 00:00:00.000,2026-01-01 00:10:00.000,SA,1,SB,2,40.0,-74.0,40.1,-74.1,member
"""
    zpath = create_test_zip(csv)
    try:
        parser = ZipCSVParser(zpath)
        batches = list(parser.iterate_rows(batch_size=10))
        total = sum(len(b) for b in batches)
        # Both reach PostgreSQL; ON CONFLICT determines the actual duplicate outcome.
        rejected = sum(1 for b in batches for row_data, _ in b if row_data.get("_rejected") == "true")
        assert rejected == 0
    finally:
        zpath.unlink(missing_ok=True)


def test_ignores_hidden_files() -> None:
    csv = """ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
1,electric_bike,2026-01-01 00:00:00.000,2026-01-01 00:10:00.000,SA,1,SB,2,40.0,-74.0,40.1,-74.1,member
"""
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("__MACOSX/._data.csv", "")
        z.writestr("data.csv", csv)
    try:
        parser = ZipCSVParser(Path(path))
        members = parser.get_csv_members()
        assert members == ["data.csv"]
    finally:
        Path(path).unlink(missing_ok=True)
