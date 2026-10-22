# Phase 3.4 — Ingestion integrity investigation

Investigation date: 2026-10-20. Database timestamps below are UTC.

**Status: January source/database reconciliation and full idempotency verification
PASSED. Exact historical termination/update attribution remains unresolved. This
does not declare every broader Phase 3 requirement complete.**

This report supersedes the earlier Phase 3.1–3.3 claims. The original report, code,
read-only SQL snapshot, full PostgreSQL dump, and server logs were preserved before
changing incident data in [before/](evidence/phase-3.4/before/).
Their checksums are in [before-sha256.json](evidence/phase-3.4/before-sha256.json).
`pg_restore --list` successfully read the preserved dump; its
[contents listing](evidence/phase-3.4/backup-contents.txt) is retained.
No incident tables were truncated, deleted, reset, or rebuilt. Tests used separate
databases; only those disposable test databases were dropped.

## 1. Root cause and evidence

The database contains an incomplete source prefix, incorrectly represented as a
completed full ingestion. Source duplication did **not** cause the missing rows.

1. An independent audit found **1,816,391 unique, non-null ride IDs**. Its archive
   SHA-256 matches the incident manifest.
2. The original database contains **330,000 distinct IDs**, all from member 1.
   Disk-backed source/database set comparison found **1,486,391 missing IDs and zero
   extra IDs**. Every one of the first 330,000 CSV rows is present; the canonical set
   is therefore exactly that prefix.
3. Grouping by `_ingested_at` gives **33 groups of exactly 10,000 rows**, with
   consecutive database IDs 1–330,000. Transaction timestamps range from
   `2026-10-20 11:55:56.151664+00` to `2026-10-20 11:57:49.091850+00`.
   See [before-membership.json](evidence/phase-3.4/before-membership.json).
4. The preserved [PostgreSQL log](evidence/phase-3.4/before/postgres.log) records:

   ```text
   2026-10-20 11:57:55.571 UTC [18693] LOG: unexpected EOF on client connection with an open transaction
   ```

   This directly establishes an interrupted database client during the incident window.
   Previously committed batches survive a disconnect; the open transaction does not.
5. The manifest nevertheless reports `completed`, read/accepted **1,816,391**, and zero
   duplicates/rejects, at `2026-10-20 11:58:11.611675+00`, after that disconnect.
6. Original `DatabaseLoader.complete_manifest()` trusted arbitrary caller counters and
   set `completed` whenever `error_message is None`. It checked neither EOF nor durable
   writes. A regression test reproduces reporting 1,816,391 accepted with only two
   canonical rows present.

**Supported causal conclusion:** only 33 batches persisted in an interrupted load,
while the completion mechanism allowed unsupported full-source counters to be recorded
as success. Trusting caller counters without persistence reconciliation is the proven
integrity defect.

**Historical attribution limit:** no earlier ingestion command/application log was found;
PostgreSQL did not log every SQL statement. The available log does not identify the
external reason for disconnect (timeout, termination, connection loss), or the caller
that subsequently marked the manifest completed. The checked-in orchestrator would not
reach successful completion on its terminated connection. A separate/nonstandard update
is consistent with the evidence, but its exact command is not established.

“37 batches at 10,000” cannot cover this archive: **182 batches** are required
(181 × 10,000 + 6,391). Thirty-seven batches would be required at the default size
50,000, but that does not establish the historical command.

Two additional loader defects are independently reproduced, without asserting that
either explains all historical missing rows:

- `ON CONFLICT (ride_id) DO NOTHING` returns success with zero affected rows for a
  conflict. The old loader ignored this, incremented `rows_inserted`, and never reached
  its `IntegrityError` duplicate handler.
- A row exception rolled back the whole current batch, then processing continued
  without correcting earlier counters. Database errors could be swallowed as generic
  rejects or duplicates. A test demonstrates two claimed insertions with only one
  newly persisted row.

## 2. Independent source counts

`scripts/reconcile_source.py` streams the ZIP using stdlib `zipfile`, text decoding,
and `csv.DictReader`, without importing ingestion code. SQLite stores ID occurrences
and distinct sets on disk, committing every 10,000 rows. Memory does not grow with ID
cardinality. Null means missing/blank after stripping whitespace; duplicates are
non-null occurrences beyond the first. Headers are excluded.

Archive: `data/cache/202601-citibike-tripdata.zip`, **353,829,139 bytes**.

SHA-256: `8d579be9515f797739f0ff907576da44f25413bc677e1286a876696a71ae9755`.

| CSV member | Data rows | Unique ride IDs | Duplicate occurrences | Null/blank IDs |
|---|---:|---:|---:|---:|
| `202601-citibike-tripdata_1.csv` | 1,000,000 | 1,000,000 | 0 | 0 |
| `202601-citibike-tripdata_2.csv` | 816,391 | 816,391 | 0 | 0 |
| Across all members | **1,816,391** | **1,816,391** | **0** | **0** |

No ZIP members were ignored. ZIP directory order is member 2 then member 1; the loader
sorts names and processes member 1 first.
Exact result: [source.json](evidence/phase-3.4/source.json).

Reproduce using a fresh SQLite filename:

```sh
python3 scripts/reconcile_source.py data/cache/202601-citibike-tripdata.zip \
  /tmp/citi-january-audit-new.sqlite
```

## 3. SQL reconciliation before repair

All SQL and actual results, including the full manifest and constraints, are in
[before/database.json](evidence/phase-3.4/before/database.json). The snapshot uses a
read-only, repeatable-read transaction.

```sql
SELECT count(*) AS rows, count(DISTINCT ride_id) AS unique_ids,
       count(*) FILTER (WHERE ride_id IS NULL OR btrim(ride_id)='') AS null_or_blank_ids,
       min(started_at_raw), max(started_at_raw),
       min(started_at_utc), max(started_at_utc)
FROM raw.trips;

SELECT count(*) FROM raw.trips_rejects;
SELECT * FROM ingest.manifest ORDER BY id;

SELECT source_file_key, source_member, count(*),
       min(started_at_raw), max(started_at_raw)
FROM raw.trips GROUP BY 1, 2 ORDER BY 1, 2;

SELECT source_file_key, source_member, reason, count(*)
FROM raw.trips_rejects GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

SELECT ride_id, count(*) FROM raw.trips
GROUP BY ride_id HAVING count(*) > 1;

SELECT _ingested_at, count(*), min(id), max(id)
FROM raw.trips GROUP BY 1 ORDER BY 1;
```

| Before-repair measurement | Actual result |
|---|---|
| Canonical rows / distinct IDs | 330,000 / 330,000 |
| Reject rows / null-or-blank IDs / duplicate groups | 0 / 0 / 0 |
| Source file | `202601-citibike-tripdata.zip` |
| Member 1 / member 2 rows | 330,000 / 0 |
| Earliest raw start | `2025-12-31 09:44:07.590` |
| Latest raw start | `2026-01-14 18:59:59.453` |
| Min/max UTC start | NULL / NULL |
| Manifest ID / month / status | 1 / `202601` / `completed` |
| Manifest read / accepted / rejected / duplicates | 1,816,391 / 1,816,391 / 0 / 0 |
| Manifest started / completed (2026-10-20) | `11:55:56.080427+00` / `11:58:11.611675+00` |
| Manifest error | NULL |

There is no `started_at` database column; it has raw text and UTC columns. Raw strings
use a consistent sortable format in this archive. UTC extrema cannot be claimed because
the loader does not populate those columns.

## 4. Code inspection and fixes

Original code is preserved under `before/`; [fix.diff](evidence/phase-3.4/fix.diff)
records the changes.

| File / function | Finding and correction |
|---|---|
| `ingest/parser.py`: `iterate_rows` | No original three-batch limit or early-stop condition. Added explicit EOF/source-row tracking, positive batch-size validation, and missing-header failure. EOF is true only after full generator exhaustion. |
| `ingest/parser.py`: `parse_row` | Removed the archive-wide in-memory ID set and parser duplicate rejection. PostgreSQL now determines duplicate outcomes after actual writes, including conflicts across members/runs. |
| `db/loader.py`: `_insert_trip` | Parameterized INSERT with `ON CONFLICT (ride_id) DO NOTHING`; no production COPY path. Returns actual affected-row outcome. |
| `db/loader.py`: `load_batch` | One explicit transaction covers canonical writes, rejects, and manifest checkpoint. Errors roll back everything in that batch and propagate. Stats return only after successful commit. |
| `db/loader.py`: `_insert_reject` | Verifies one reject row was written. |
| `db/loader.py`: `create_manifest` | Captures database baselines and resets status, counters, error, and completion timestamp on each attempt. Old upsert could retain `completed` during an interrupted rerun. |
| `db/loader.py`: `complete_manifest` | Requires EOF, accounting equation, independent row expectation when supplied, matching durable counters, and matching database growth. |
| `db/loader.py`: `connect` | Explicit transaction boundaries and a database-scoped advisory ingestion lock serialize cooperating runs. |
| `ingest/orchestrator.py`: `ingest_archive` | Shared cached-archive path for month ingestion and verification. Updates totals after commit, verifies completion, marks catchable interruptions failed, preserves original errors if failure recording also fails, always closes connection. |
| `ingest/orchestrator.py`: `__init__` | Corrected project root from `src/` to repository root; preserves absolute configured paths. Old code used `src/data/cache`, where failed-download temporary files were present. |
| `cli/main.py`: `ingest` | Inspected, unchanged: delegates to month orchestration and exits nonzero on exceptions. Integration test exercises this path using local archive and mocked remote listing/download. |

No dbt, forecasting, or new analytical features were implemented.

## 5. Accounting invariants

For a successfully exhausted source:

```text
rows_read = rows_accepted + duplicates_detected + rows_rejected
          = newly_inserted + duplicates_or_existing + rejected
```

- `rows_read`: source data rows durably classified in committed batches.
- `rows_accepted`: newly inserted canonical rows this attempt, based on affected rows
  and confirmed commit, not attempted writes.
- `duplicates_detected`: valid source rows skipped by the unique ride-ID conflict,
  against earlier source rows or previous runs.
- `rows_rejected`: validation failures durably written to the reject table. A rejected
  row is not also counted as a duplicate.
- Database/infrastructure errors are not source rejects: they fail the attempt and
  roll back the current batch.

For failed/interrupted runs, durable `rows_read` covers only committed batches.
Rows parsed into an uncommitted batch remain unprocessed for durable accounting and are
retried. The parser's in-process count may exceed the manifest checkpoint on failure.
Full-source reconciliation is never claimed for that attempt. A hard kill/lost connection
can leave `running` with a committed checkpoint; catchable interruption becomes `failed`.

Completion checks canonical growth equals accepted, reject growth equals rejected,
and durable counters equal orchestrator totals. The full verification also supplies the
independent source count. Separate disk-backed set comparisons check every source/database
ride ID, rather than relying solely on equal cardinalities.

## 6. Tests and controlled verification

```sh
CITI_TEST_POSTGRES=1 PYTHONPATH=src python3 -m pytest -q
```

**28 passed**, including real PostgreSQL tests.
Output: [tests.txt](evidence/phase-3.4/tests.txt).

Coverage: five batches, multiple members, partial final batch, cross-member/existing
conflicts, exact accounting, database constraint errors, rollback after partial writes,
deferred failure at COMMIT, atomic checkpoints, interruption/restart, unfinished
connection, stale-status reset, EOF guard, wrong source totals, invalid accounting,
checkpoint mismatch, persistence mismatch, malformed later member, CLI delegation,
independent SQLite duplicate/null counting, and original false-count/completion defects.

Synthetic archive: **13 rows**, two members, batch size 3 (five batches).

| Attempt | Read | Inserted | Duplicate/existing | Rejected | Canonical total |
|---|---:|---:|---:|---:|---:|
| Fresh synthetic | 13 | 9 | 2 | 2 | 9 |
| Synthetic rerun | 13 | 0 | 11 | 2 | 9 |

Rejects are recorded per attempt: four audit rows after this rerun. Canonical records
are idempotent.

Bounded real data: first **20,003 rows of each member**, independently copied using
stdlib CSV, total **40,006**, batch size 10,000 (five batches), in a separate temporary
database after synthetic checks passed.

| Attempt | Read | Inserted | Duplicate/existing | Rejected | Canonical total |
|---|---:|---:|---:|---:|---:|
| Fresh bounded | 40,006 | 40,006 | 0 | 0 | 40,006 |
| Bounded rerun | 40,006 | 0 | 40,006 | 0 | 40,006 |

Both completed with matching manifest and database counts.
Evidence: [bounded.json](evidence/phase-3.4/bounded.json),
[bounded.log](evidence/phase-3.4/bounded.log).

## 7. Full January repair and idempotency

Original rides were retained; no destructive rebuild was necessary. The original
manifest was preserved before the normal ingestion upsert began.

The full repair processed **182 batches** of at most 10,000 rows, reached EOF, and took
**685.56 seconds** (ingestion, before independent set comparison).

| Attempt | Source/read | Newly inserted | Duplicate/existing | Rejected | DB rows/distinct |
|---|---:|---:|---:|---:|---:|
| Full repair | 1,816,391 | 1,486,391 | 330,000 | 0 | 1,816,391 / 1,816,391 |
| Full rerun | 1,816,391 | 0 | 1,816,391 | 0 | 1,816,391 / 1,816,391 |

```text
1,816,391 = 1,486,391 + 330,000 + 0
DB after = 330,000 before + 1,486,391 new = 1,816,391
Rerun: 1,816,391 = 0 + 1,816,391 + 0
DB after rerun = 1,816,391 + 0 = 1,816,391
```

Direct SQL confirms zero null/blank IDs, zero duplicate groups, and zero reject rows.
Both members belong to `202601-citibike-tripdata.zip`:

| Member | Persisted rows | Earliest raw start | Latest raw start |
|---|---:|---|---|
| `202601-citibike-tripdata_1.csv` | 1,000,000 | `2025-12-30 23:30:09.507` | `2026-01-14 18:59:59.453` |
| `202601-citibike-tripdata_2.csv` | 816,391 | `2025-12-31 23:32:23.856` | `2026-01-31 23:56:50.315` |

Overall raw start range is `2025-12-30 23:30:09.507` through
`2026-01-31 23:56:50.315`; UTC extrema remain NULL.

Full-repair manifest ID 1 is `completed`, read **1,816,391**, accepted **1,486,391**,
duplicates **330,000**, rejects **0**, error NULL, started
`2026-10-20 12:12:16.428045+00`, completed `2026-10-20 12:23:41.590425+00`.
Evidence: [full.json](evidence/phase-3.4/full.json),
[full-database.json](evidence/phase-3.4/full-database.json),
[full.log](evidence/phase-3.4/full.log).

A repeatable-read midpoint [SQL snapshot](evidence/phase-3.4/during-full-database.json)
also demonstrated correct running-state accounting: **1,320,000 rows** persisted,
manifest `running`, **990,000 new + 330,000 existing**, no completion timestamp.

Independent final set comparison passed: **zero missing source IDs and zero extra
database IDs**, recorded in [full-membership.json](evidence/phase-3.4/full-membership.json).

The full rerun also processed **182 batches**, taking **673.33 seconds** before the
independent set comparison. Latest manifest ID 1 is `completed`, read **1,816,391**,
accepted **0**, duplicates **1,816,391**, rejected **0**, error NULL, started
`2026-10-20 12:25:33.270708+00`, completed `2026-10-20 12:36:45.832049+00`.
Its accepted count is attempt-specific, not cumulative; the prior successful repair
manifest remains preserved in `full-database.json`.

The rerun's SQL counts, per-member counts, date extrema, null/duplicate counts, and
reject count are unchanged. Independent set comparison again found **zero missing IDs
and zero extra IDs**. The entire recorded grouping by persisted `_ingested_at` and
database ID ranges is identical before and after rerun.

Evidence: [rerun.json](evidence/phase-3.4/rerun.json),
[rerun-database.json](evidence/phase-3.4/rerun-database.json),
[rerun-membership.json](evidence/phase-3.4/rerun-membership.json),
[rerun.log](evidence/phase-3.4/rerun.log).

A [rerun midpoint SQL snapshot](evidence/phase-3.4/during-rerun-database.json) confirms
the stale-completion bug is fixed: status `running`, no completion timestamp,
**1,180,000 read/existing**, **zero accepted**, and canonical total **1,816,391**.

Final evidence cross-checks passed: both logs contain 182 committed batch results;
both result objects exactly match their persisted manifest counters; both accounting
equations hold; SQL counts/distinct counts equal the independent source total; rejects
and duplicate groups are zero; both complete ID sets match.

```sh
PYTHONPATH=src python3 scripts/verify_ingestion.py before
PYTHONPATH=src python3 scripts/verify_ingestion.py bounded
PYTHONPATH=src python3 scripts/verify_ingestion.py full
PYTHONPATH=src python3 scripts/verify_ingestion.py rerun
```

Membership verification uses `/tmp/citi-january-audit.sqlite`, created by the independent
audit, and compares IDs on disk rather than loading the source/database sets into memory.
For a newly reproduced audit, set
`CITI_AUDIT_DB=/tmp/citi-january-audit-new.sqlite` when invoking the verification script.
The `before` command describes the original 330,000-row incident state and must not be
used to overwrite its preserved evidence after repair. Similarly, use new output paths
or a copied workspace for subsequent investigations to retain this evidence.

## 8. Remaining limitations

- Historical client termination reason and the caller issuing false completion are
  unavailable. The conflict-counting bug alone does not explain the missing source IDs.
- UTC timestamps/duration remain unpopulated. Timestamp unit tests do not establish
  persisted timestamp correctness. Unrelated transformation work is out of scope.
- Rejects are per-attempt audit records and can repeat on rerun. January's zero-reject
  case does not establish reject-table idempotency for other archives.
- Manifest retains the latest attempt, not full run history; this investigation
  preserves important attempts externally in the evidence directory.
- Advisory locking controls cooperating loaders, not arbitrary manual SQL writers.
  Persistence deltas assume no unrelated external mutations during ingestion.
- Hard kills/database loss may leave `running`; the fixed loader cannot report
  completion without EOF and successful reconciliation.
- Existing validation/numeric coercion policies are not expanded.
- Full verification uses the cached path shared with the CLI. No live S3 download was
  needed; remote listing/download behavior was not revalidated.
