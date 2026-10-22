"""Read-only, consistent incident snapshot, including executable SQL and full manifest."""
import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from citi_bike_ingest.config.settings import Settings

QUERIES = {
    "canonical": """SELECT count(*) AS rows, count(DISTINCT ride_id) AS unique_ids,
        count(*) FILTER (WHERE ride_id IS NULL OR btrim(ride_id)='') AS null_or_blank_ids,
        min(started_at_raw) AS earliest_started_at_raw,
        max(started_at_raw) AS latest_started_at_raw,
        min(started_at_utc) AS earliest_started_at_utc,
        max(started_at_utc) AS latest_started_at_utc FROM raw.trips""",
    "rejects": "SELECT count(*) AS rows FROM raw.trips_rejects",
    "manifest": "SELECT * FROM ingest.manifest ORDER BY id",
    "members": """SELECT source_file_key, source_member, count(*) AS rows,
        min(started_at_raw) AS earliest_started_at_raw,
        max(started_at_raw) AS latest_started_at_raw
        FROM raw.trips GROUP BY 1,2 ORDER BY 1,2""",
    "reject_members": """SELECT source_file_key, source_member, reason, count(*) AS rows
        FROM raw.trips_rejects GROUP BY 1,2,3 ORDER BY 1,2,3""",
    "duplicates": """SELECT ride_id, count(*) FROM raw.trips
        GROUP BY ride_id HAVING count(*) > 1""",
    "constraints": """SELECT conrelid::regclass::text AS table_name, conname,
        pg_get_constraintdef(oid) AS definition FROM pg_constraint
        WHERE connamespace IN ('raw'::regnamespace, 'ingest'::regnamespace)""",
}


def snapshot(destination):
    with psycopg.connect(Settings().database_url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        result = {name: {"sql": sql, "results": conn.execute(sql).fetchall()}
                  for name, sql in QUERIES.items()}
    Path(destination).write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    snapshot(sys.argv[1])
