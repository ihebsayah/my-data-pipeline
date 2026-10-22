"""Independent CSV audit: stdlib CSV + disk-backed SQLite, no ingestion imports."""
import csv
import hashlib
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path


def audit(archive, database):
    conn = sqlite3.connect(database)
    conn.executescript("""
        CREATE TABLE ids (ride_id TEXT PRIMARY KEY, occurrences INTEGER NOT NULL);
        CREATE TABLE member_ids (ride_id TEXT PRIMARY KEY, occurrences INTEGER NOT NULL);
    """)
    results = []
    with zipfile.ZipFile(archive) as z:
        ignored = []
        for info in z.infolist():
            name = info.filename
            if (info.is_dir() or not name.lower().endswith(".csv")
                    or "__MACOSX" in name or Path(name).name.startswith(".")):
                ignored.append(name)
                continue
            conn.execute("DELETE FROM member_ids")
            count = nulls = 0
            with z.open(info) as stream:
                reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"))
                for row in reader:
                    count += 1
                    ride_id = (row.get("ride_id") or "").strip()
                    if not ride_id:
                        nulls += 1
                        continue
                    for table in ("ids", "member_ids"):
                        conn.execute(
                            f"INSERT INTO {table} VALUES (?, 1) ON CONFLICT(ride_id) "
                            "DO UPDATE SET occurrences=occurrences+1", (ride_id,))
                    if count % 10000 == 0:
                        conn.commit()
            conn.commit()
            unique = conn.execute("SELECT count(*) FROM member_ids").fetchone()[0]
            results.append(dict(filename=name, rows=count, unique_ride_ids=unique,
                                duplicate_ride_ids=count - nulls - unique, null_ride_ids=nulls))
    total = sum(r["rows"] for r in results)
    nulls = sum(r["null_ride_ids"] for r in results)
    unique = conn.execute("SELECT count(*) FROM ids").fetchone()[0]
    with open(archive, "rb") as f:
        sha = hashlib.file_digest(f, "sha256").hexdigest()
    conn.close()
    return dict(archive=str(archive), sha256=sha, members=results, ignored=ignored,
                rows=total, unique_ride_ids=unique, duplicate_ride_ids=total-nulls-unique,
                null_ride_ids=nulls)


if __name__ == "__main__":
    print(json.dumps(audit(sys.argv[1], sys.argv[2]), indent=2))
