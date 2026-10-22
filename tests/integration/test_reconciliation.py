"""Run with CITI_TEST_POSTGRES=1; creates/drops only UUID-named test databases."""
import csv
import io
import os
import runpy
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql

from citi_bike_ingest.config.settings import Settings
from citi_bike_ingest.db.loader import DatabaseLoader
from citi_bike_ingest.ingest.orchestrator import IngestionOrchestrator
from citi_bike_ingest.ingest.parser import MODERN_SCHEMA, ZipCSVParser

pytestmark = pytest.mark.skipif(
    os.environ.get("CITI_TEST_POSTGRES") != "1", reason="Explicit local PostgreSQL opt-in"
)


@pytest.fixture
def settings():
    base = Settings()
    name = "citi_reconciliation_test_" + uuid.uuid4().hex
    with psycopg.connect(base.database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield base.model_copy(update={"postgres_db": name, "batch_size": 3})
    with psycopg.connect(base.database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def loader(settings):
    loader = DatabaseLoader(settings)
    loader.connect()
    loader.ensure_schema()
    yield loader
    loader.close()


def row(ride_id):
    return {"ride_id": ride_id, "started_at": "2026-01-01 00:00:00",
            "ended_at": "2026-01-01 00:10:00"}


def archive(tmp_path):
    path = tmp_path / "synthetic.zip"
    rows = [row(str(i)) for i in range(9)]
    rows += [row("0"), row("8"), row(""), {**row("bad"), "started_at": ""}]
    with zipfile.ZipFile(path, "w") as z:
        for name, subset in [("a.csv", rows[:7]), ("b.csv", rows[7:])]:
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=MODERN_SCHEMA)
            writer.writeheader()
            writer.writerows(subset)
            z.writestr(name, stream.getvalue())
    return path


def manifest(loader):
    return loader.create_manifest("test.zip", "202601", "test", 1, "test.zip")


def test_five_batches_multiple_members_exact_accounting_and_rerun(settings, tmp_path):
    path = archive(tmp_path)
    parser = ZipCSVParser(path)
    assert [len(b) for b in parser.iterate_rows(3)] == [3, 3, 3, 3, 1]
    assert parser.exhausted
    orchestrator = IngestionOrchestrator(settings)
    first = orchestrator.ingest_archive(path, "test.zip", "202601", expected_rows=13)
    assert (first["rows_read"], first["rows_accepted"], first["duplicates_detected"],
            first["rows_rejected"]) == (13, 9, 2, 2)
    second = orchestrator.ingest_archive(path, "test.zip", "202601", expected_rows=13)
    assert (second["rows_read"], second["rows_accepted"], second["duplicates_detected"],
            second["rows_rejected"]) == (13, 0, 11, 2)
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM raw.trips").fetchone() == (9,)
        assert conn.execute(
            "SELECT status, rows_read, rows_accepted, rows_rejected, duplicates_detected "
            "FROM ingest.manifest").fetchone() == ("completed", 13, 0, 2, 11)


def test_conflict_uses_actual_affected_rows(loader):
    mid = manifest(loader)
    batch = [(row("a"), "a.csv"), (row("a"), "b.csv")]
    stats = loader.load_batch(batch, "test.zip", manifest_id=mid)
    assert (stats.rows_inserted, stats.duplicates_skipped) == (1, 1)
    assert loader.persistence_counts() == (1, 0)


@pytest.mark.parametrize("error", [RuntimeError("write failed"), KeyboardInterrupt()])
def test_partial_batch_rolls_back_with_checkpoint(loader, monkeypatch, error):
    mid = manifest(loader)
    original = loader._insert_trip

    def fail(row_data, member, file_key):
        if row_data["ride_id"] == "b":
            raise error
        return original(row_data, member, file_key)

    monkeypatch.setattr(loader, "_insert_trip", fail)
    with pytest.raises(type(error)):
        loader.load_batch([(row("a"), "a.csv"), (row("b"), "a.csv")],
                          "test.zip", manifest_id=mid)
    assert loader.persistence_counts() == (0, 0)
    assert loader._conn.execute("SELECT rows_read FROM ingest.manifest").fetchone() == (0,)


def test_database_error_is_not_suppressed(loader):
    mid = manifest(loader)
    loader._conn.execute("ALTER TABLE raw.trips ADD CHECK (ride_id <> 'b')")
    with pytest.raises(psycopg.errors.CheckViolation):
        loader.load_batch([(row("a"), "a"), (row("b"), "a")], "test.zip", manifest_id=mid)
    assert loader.persistence_counts() == (0, 0)


def test_commit_failure_rolls_back_data_and_manifest(loader):
    mid = manifest(loader)
    loader._conn.execute("CREATE TABLE allowed (ride_id TEXT PRIMARY KEY)")
    loader._conn.execute(
        "ALTER TABLE raw.trips ADD FOREIGN KEY (ride_id) REFERENCES allowed(ride_id) "
        "DEFERRABLE INITIALLY DEFERRED")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        loader.load_batch([(row("a"), "a")], "test.zip", manifest_id=mid)
    assert loader.persistence_counts() == (0, 0)
    assert loader._conn.execute("SELECT rows_read FROM ingest.manifest").fetchone() == (0,)


def test_unfinished_connection_cannot_report_completed(loader, settings):
    mid = manifest(loader)
    loader.load_batch([(row("a"), "a")], "test.zip", manifest_id=mid)
    loader.close()
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute(
            "SELECT status, rows_read, rows_accepted FROM ingest.manifest"
        ).fetchone() == ("running", 1, 1)


def test_interruption_retains_only_committed_checkpoints(settings, tmp_path, monkeypatch):
    path = archive(tmp_path)
    original = DatabaseLoader.load_batch
    calls = 0

    def interrupted(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise KeyboardInterrupt()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DatabaseLoader, "load_batch", interrupted)
    with pytest.raises(KeyboardInterrupt):
        IngestionOrchestrator(settings).ingest_archive(path, "test.zip", "202601")
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM raw.trips").fetchone() == (9,)
        assert conn.execute(
            "SELECT status, rows_read, rows_accepted FROM ingest.manifest"
        ).fetchone() == ("failed", 9, 9)
    monkeypatch.setattr(DatabaseLoader, "load_batch", original)
    result = IngestionOrchestrator(settings).ingest_archive(path, "test.zip", "202601", 13)
    assert result["rows_accepted"] == 0
    assert result["duplicates_detected"] == 11


def test_completion_requires_eof_checkpoint_accounting_and_persistence(loader):
    mid = manifest(loader)
    with pytest.raises(ValueError, match="EOF"):
        loader.complete_manifest(mid, 0, 0, 0, 0)
    with pytest.raises(ValueError, match="accounting"):
        loader.complete_manifest(mid, 1, 0, 0, 0, source_exhausted=True)
    with pytest.raises(ValueError, match="source count"):
        loader.complete_manifest(mid, 0, 0, 0, 0, source_exhausted=True, expected_rows=1)
    with pytest.raises(ValueError, match="checkpoint"):
        loader.complete_manifest(mid, 1, 1, 0, 0, source_exhausted=True)
    loader.load_batch([(row("a"), "a")], "test.zip", manifest_id=mid)
    loader._conn.execute("DELETE FROM raw.trips WHERE ride_id='a'")
    with pytest.raises(ValueError, match="persistence"):
        loader.complete_manifest(mid, 1, 1, 0, 0, source_exhausted=True)
    assert loader._conn.execute("SELECT status FROM ingest.manifest").fetchone() == ("running",)


def test_rerun_resets_completed_manifest(loader):
    mid = manifest(loader)
    loader.complete_manifest(mid, 0, 0, 0, 0, source_exhausted=True)
    assert manifest(loader) == mid
    assert loader._conn.execute(
        "SELECT status, completed_at, rows_read, error_message FROM ingest.manifest"
    ).fetchone() == ("running", None, 0, None)


def test_cli_month_path_uses_same_reconciled_loader(settings, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from citi_bike_ingest.cli import main
    from citi_bike_ingest.ingest.downloader import Downloader
    from citi_bike_ingest.ingest.s3_listing import CitiBikeS3Listing

    path = archive(tmp_path)
    obj = SimpleNamespace(key="test.zip", size=path.stat().st_size)
    monkeypatch.setattr(CitiBikeS3Listing, "fetch_listing", lambda self: [obj])
    monkeypatch.setattr(CitiBikeS3Listing, "find_nyc_month", lambda *args: obj)
    monkeypatch.setattr(Downloader, "download", lambda *args: path)
    monkeypatch.setattr(main, "IngestionOrchestrator", lambda: IngestionOrchestrator(settings))
    result = CliRunner().invoke(main.app, ["--month", "202601"])
    assert result.exit_code == 0, result.output
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute(
            "SELECT status, rows_read, rows_accepted FROM ingest.manifest"
        ).fetchone() == ("completed", 13, 9)


def test_invalid_later_member_cannot_complete(settings, tmp_path):
    path = archive(tmp_path)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("z.csv", "wrong_header\nvalue\n")
    with pytest.raises(ValueError, match="Invalid header"):
        IngestionOrchestrator(settings).ingest_archive(path, "test.zip", "202601")
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute(
            "SELECT status, rows_read FROM ingest.manifest"
        ).fetchone() == ("failed", 12)


def test_old_loader_reproduces_false_insert_and_rollback_counts(settings, monkeypatch):
    old_type = runpy.run_path("docs/evidence/phase-3.4/before/loader.py")["DatabaseLoader"]
    old = old_type(settings)
    old.connect()
    old.ensure_schema()
    try:
        first = old.load_batch([(row("a"), "a")], "test.zip")
        repeat = old.load_batch([(row("a"), "a")], "test.zip")
        assert first.rows_inserted == repeat.rows_inserted == 1
        assert repeat.duplicates_skipped == 0
        assert old._conn.execute("SELECT count(*) FROM raw.trips").fetchone() == (1,)
        old._conn.commit()
        original = old._insert_trip

        def fail(data, member, key):
            if data["ride_id"] == "error":
                raise ValueError("injected row failure")
            return original(data, member, key)

        monkeypatch.setattr(old, "_insert_trip", fail)
        stats = old.load_batch([(row(i), "a") for i in ("b", "error", "c")], "test.zip")
        assert (stats.rows_inserted, stats.rows_rejected) == (2, 1)
        assert old._conn.execute("SELECT ride_id FROM raw.trips ORDER BY ride_id").fetchall() == [
            ("a",), ("c",)]
        mid = old.create_manifest("test.zip", "202601", "test", 1, "test.zip")
        old.complete_manifest(mid, 1816391, 1816391, 0, 0)
        assert old._conn.execute(
            "SELECT status, rows_accepted FROM ingest.manifest"
        ).fetchone() == ("completed", 1816391)
    finally:
        old.close()
