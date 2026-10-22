from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from citi_bike_ingest.config.settings import Settings

logger = logging.getLogger(__name__)

UTC_TZ = timezone.utc


@dataclass
class LoadStats:
    """Database load statistics."""

    rows_inserted: int = 0
    rows_rejected: int = 0
    duplicates_skipped: int = 0
    manifest_id: Optional[int] = None


class DatabaseLoader:
    """Load data into PostgreSQL."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._conn: Optional[psycopg.Connection[Any]] = None

    def connect(self) -> None:
        self._conn = psycopg.connect(self.settings.database_url)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def ensure_schema(self) -> None:
        if not self._conn:
            raise RuntimeError("Not connected")
        with self._conn.cursor() as cur:
            cur.execute("""
                CREATE SCHEMA IF NOT EXISTS raw;
                CREATE SCHEMA IF NOT EXISTS ingest;

                CREATE TABLE IF NOT EXISTS ingest.manifest (
                    id SERIAL PRIMARY KEY,
                    source TEXT NOT NULL,
                    file_key TEXT NOT NULL,
                    local_path TEXT,
                    checksum_sha256 TEXT,
                    bytes BIGINT,
                    month TEXT,
                    status TEXT,
                    rows_read BIGINT,
                    rows_accepted BIGINT,
                    rows_rejected BIGINT,
                    duplicates_detected BIGINT,
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    error_message TEXT,
                    UNIQUE (file_key, month)
                );

                CREATE TABLE IF NOT EXISTS raw.trips (
                    id BIGSERIAL PRIMARY KEY,
                    ride_id TEXT,
                    rideable_type TEXT,
                    started_at_raw TEXT,
                    ended_at_raw TEXT,
                    started_at_utc TIMESTAMPTZ,
                    ended_at_utc TIMESTAMPTZ,
                    trip_duration_sec INTEGER,
                    start_station_name TEXT,
                    start_station_id TEXT,
                    end_station_name TEXT,
                    end_station_id TEXT,
                    start_lat NUMERIC,
                    start_lng NUMERIC,
                    end_lat NUMERIC,
                    end_lng NUMERIC,
                    member_casual TEXT,
                    source_member TEXT,
                    source_file_key TEXT,
                    _ingested_at TIMESTAMPTZ DEFAULT NOW(),
                    CONSTRAINT raw_trips_ride_id_unique UNIQUE (ride_id)
                );

                CREATE TABLE IF NOT EXISTS raw.trips_rejects (
                    id BIGSERIAL PRIMARY KEY,
                    reason TEXT,
                    ride_id TEXT,
                    started_at_raw TEXT,
                    ended_at_raw TEXT,
                    row_data JSONB,
                    source_member TEXT,
                    source_file_key TEXT,
                    _ingested_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_raw_trips_started_at_utc ON raw.trips (started_at_utc);
                CREATE INDEX IF NOT EXISTS idx_raw_trips_ride_id ON raw.trips (ride_id);
                CREATE INDEX IF NOT EXISTS idx_raw_trips_rejects_reason ON raw.trips_rejects (reason);
            """)
        self._conn.commit()

    def create_manifest(
        self, file_key: str, month: str, checksum: str, bytes_count: int, local_path: str
    ) -> int:
        if not self._conn:
            raise RuntimeError("Not connected")
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ingest.manifest (
                    source, file_key, local_path, checksum_sha256, bytes, month,
                    status, started_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (file_key, month) DO UPDATE
                SET local_path = EXCLUDED.local_path,
                    checksum_sha256 = EXCLUDED.checksum_sha256,
                    bytes = EXCLUDED.bytes,
                    started_at = NOW()
                RETURNING id
                """,
                ("s3://tripdata", file_key, local_path, checksum, bytes_count, month, "running"),
            )
            row = cur.fetchone()
            manifest_id = row[0] if row else 0
        self._conn.commit()
        return manifest_id

    def complete_manifest(
        self,
        manifest_id: int,
        rows_read: int,
        rows_accepted: int,
        rows_rejected: int,
        duplicates_detected: int,
        error_message: Optional[str] = None,
    ) -> None:
        if not self._conn:
            raise RuntimeError("Not connected")
        status = "completed" if error_message is None else "failed"
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingest.manifest
                SET status=%s, rows_read=%s, rows_accepted=%s, rows_rejected=%s,
                    duplicates_detected=%s, completed_at=NOW(), error_message=%s
                WHERE id=%s
                """,
                (status, rows_read, rows_accepted, rows_rejected, duplicates_detected, error_message, manifest_id),
            )
        self._conn.commit()

    def load_batch(
        self,
        batch_rows: List[tuple[Dict[str, str], str]],
        file_key: str,
        source_member: str | None = None,
    ) -> LoadStats:
        if not self._conn:
            raise RuntimeError("Not connected")
        stats = LoadStats()
        for row_data, member in batch_rows:
            if row_data.get("_rejected") == "true":
                stats.rows_rejected += 1
                reason = row_data.get("_reason", "unknown")
                self._insert_reject(row_data, reason, member, file_key)
            else:
                try:
                    self._insert_trip(row_data, member, file_key)
                    stats.rows_inserted += 1
                except psycopg.IntegrityError:
                    stats.duplicates_skipped += 1
                    self._conn.rollback()
                    continue
                except Exception:
                    self._conn.rollback()
                    stats.rows_rejected += 1
                    self._insert_reject(row_data, "insert_error", member, file_key)
                    continue
        self._conn.commit()
        return stats

    def _insert_trip(self, row: Dict[str, str], member: str, file_key: str) -> None:
        if not self._conn:
            return
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw.trips (
                    ride_id, rideable_type, started_at_raw, ended_at_raw,
                    start_station_name, start_station_id, end_station_name, end_station_id,
                    start_lat, start_lng, end_lat, end_lng, member_casual,
                    source_member, source_file_key
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (ride_id) DO NOTHING
                """,
                (
                    row.get("ride_id", ""),
                    row.get("rideable_type", ""),
                    row.get("started_at", ""),
                    row.get("ended_at", ""),
                    row.get("start_station_name", ""),
                    row.get("start_station_id", ""),
                    row.get("end_station_name", ""),
                    row.get("end_station_id", ""),
                    self._to_num(row.get("start_lat", "")),
                    self._to_num(row.get("start_lng", "")),
                    self._to_num(row.get("end_lat", "")),
                    self._to_num(row.get("end_lng", "")),
                    row.get("member_casual", ""),
                    member,
                    file_key,
                ),
            )

    def _insert_reject(
        self, row: Dict[str, str], reason: str, member: str, file_key: str
    ) -> None:
        if not self._conn:
            return
        import json

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw.trips_rejects (
                    reason, ride_id, started_at_raw, ended_at_raw,
                    row_data, source_member, source_file_key
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    reason,
                    row.get("ride_id", ""),
                    row.get("started_at", ""),
                    row.get("ended_at", ""),
                    json.dumps(row),
                    member,
                    file_key,
                ),
            )

    @staticmethod
    def _to_num(s: str) -> Optional[float]:
        s2 = s.strip()
        if not s2:
            return None
        try:
            return float(s2)
        except ValueError:
            return None
