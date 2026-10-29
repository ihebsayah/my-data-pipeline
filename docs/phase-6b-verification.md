# Phase 6B — Historical Expansion and Data Integrity

Updated: 2026-10-27T21:24:05.705901+00:00

**Readiness: PASS.** Phase 6B is ready for architectural review.

## 1. Git baseline

Annotated `v0.5b` resolves to `1bc300fd9f3ea08474693982270d7948c5c09aec`. Approved Phase 6A was already committed as `4202a06`; incremental staging, bulk loading and initial backfill were committed as `46a23c4`. Current HEAD: `46a23c4067da21a40a8e6b24a6d6e8c6e55a8b3c`. Completion changes remain available for review. No push performed.

## 2. Storage preflight and staging decision

Phase 4C measured 12,056,963 raw/staged rows, a 9,797 MiB database and 39m31s full dbt refresh. Phase 6A estimated 35,541,709 additional rows, 28.18 GiB additional DB storage, 6.46 GiB archives, 43.30 GiB free-space requirement and 11.48 hours total work. A full staging rebuild was judged impractical; incremental staging parses only ride IDs absent from staging. Raw canonical records are immutable. Full refresh is required after changing timestamp semantics. Unique ride-ID indexes remain enforced.

A prior staging build failed with a unique-key violation. Recovery is serialized with the existing backfill and staging processes; the failed build is not counted as success. Existing timestamp parsing/DST macros are unchanged.

Resumption preflight is recorded in `data/processed/phase6b/preflight-resumption.json`.

## 3. Per-month archive and ingestion reconciliation

Archives are audited independently with stdlib CSV, including every CSV member. Source SHA-256, byte size, row accounting, manifest status, raw counts and unique ride IDs must agree before continuing. Raw sources and rejection audit remain retained.

| Month | Bytes | Members | Source rows | Unique accepted IDs | Rejected | Reconciled |
|---|---:|---:|---:|---:|---:|---|
| 202501 | 414213312 | 3 | 2124475 | 2124475 | 0 | PASS |
| 202502 | 396008433 | 3 | 2031257 | 2031257 | 0 | PASS |
| 202503 | 618260276 | 4 | 3168271 | 3168271 | 0 | PASS |
| 202504 | 727249076 | 4 | 3724596 | 3724596 | 0 | PASS |
| 202505 | 844446636 | 5 | 4325553 | 4325553 | 0 | PASS |
| 202506 | 929043436 | 5 | 4759345 | 4759345 | 0 | PASS |
| 202507 | 973875638 | 5 | 4988053 | 4988053 | 0 | PASS |
| 202508 | 1000258641 | 6 | 5121533 | 5121533 | 0 | PASS |
| 202509 | 1032411368 | 6 | 5287447 | 5287447 | 0 | PASS |

SHA-256 per archive:

- 202501: `ed3b0138903960afe50386ab44fec8bf52d69474b26f5c0af83e648094aea096`
- 202502: `e4860762fb07561bbda251a20223d996e39c6c2c8bc2592896507bfbaa40ce78`
- 202503: `f5cc8c5a4ed99e719a4a9d72014b3b206ab03c2b23ce05a2af4be6564444833f`
- 202504: `5bda09477bb675e5a481a6f68acd367c2cae48e745a97011ca8a22c0073cece4`
- 202505: `0f5edc46361c4581c614a19cb697271b6f04143f6648b56da8fc18f7310ab103`
- 202506: `fdc15ebc98ddef6ffa6825a71616e2f704def8376590881f91b0109cd2d81f9b`
- 202507: `5c336f33986e229203308f1e177f42d46bce3c6a3a85aac7d412cd7109b3253b`
- 202508: `c15c6f1a3c3084fa03c012a20f88474907cca74119c31e58eb877b753dbc19b1`
- 202509: `b1721f1f9306daf2ca5aa358efe64c70da7c58cda82ae595692bb2d7cf2d5e46`

Full source reruns and incremental staging rerun evidence are stored in `finalization.json`; accepted rows must be zero on each source rerun. The isolated ingestion suite verifies interruption rollback and restart accounting.

## 4. Cumulative raw and staging counts

```json
{
  "raw_rows": 47587493,
  "raw_distinct_ride_ids": 47587493,
  "staging_rows": 47587493,
  "reject_rows": 0,
  "database_bytes": 40896945175,
  "raw_trips_bytes": 20858585088,
  "stg_trips_bytes": 20026130432,
  "staging_relkind": "r",
  "staging_indexes": [
    "CREATE INDEX \"3bb0c2d8fa14a82399db75ceb12b4bdc\" ON staging.stg_trips USING btree (started_at_utc)",
    "CREATE INDEX d2a51c0af8a5e9c14432c340b54d9411 ON staging.stg_trips USING btree (started_at_status)",
    "CREATE UNIQUE INDEX cc19cda39186bbed40fc3834a5d7ec03 ON staging.stg_trips USING btree (ride_id)"
  ],
  "files": [
    {
      "source_file_key": "202501-citibike-tripdata.zip",
      "rows": "2124475",
      "distinct_ride_ids": "2124475",
      "started_min": "2024-12-30 23:41:25.635",
      "started_max": "2025-01-31 23:58:14.634"
    },
    {
      "source_file_key": "202502-citibike-tripdata.zip",
      "rows": "2031257",
      "distinct_ride_ids": "2031257",
      "started_min": "2025-01-31 09:26:50.490",
      "started_max": "2025-02-28 23:58:02.884"
    },
    {
      "source_file_key": "202503-citibike-tripdata.zip",
      "rows": "3168271",
      "distinct_ride_ids": "3168271",
      "started_min": "2025-02-28 08:43:52.291",
      "started_max": "2025-03-31 23:56:56.495"
    },
    {
      "source_file_key": "202504-citibike-tripdata.zip",
      "rows": "3724596",
      "distinct_ride_ids": "3724596",
      "started_min": "2025-03-31 02:22:50.612",
      "started_max": "2025-04-30 23:58:20.015"
    },
    {
      "source_file_key": "202505-citibike-tripdata.zip",
      "rows": "4325553",
      "distinct_ride_ids": "4325553",
      "started_min": "2025-04-29 23:02:37.671",
      "started_max": "2025-05-31 23:58:06.793"
    },
    {
      "source_file_key": "202506-citibike-tripdata.zip",
      "rows": "4759345",
      "distinct_ride_ids": "4759345",
      "started_min": "2025-05-30 23:47:29.703",
      "started_max": "2025-06-30 23:58:25.278"
    },
    {
      "source_file_key": "202507-citibike-tripdata.zip",
      "rows": "4988053",
      "distinct_ride_ids": "4988053",
      "started_min": "2025-06-29 23:16:34.475",
      "started_max": "2025-07-31 23:57:27.342"
    },
    {
      "source_file_key": "202508-citibike-tripdata.zip",
      "rows": "5121533",
      "distinct_ride_ids": "5121533",
      "started_min": "2025-07-30 23:00:23.818",
      "started_max": "2025-08-31 23:58:17.885"
    },
    {
      "source_file_key": "202509-citibike-tripdata.zip",
      "rows": "5287447",
      "distinct_ride_ids": "5287447",
      "started_min": "2025-08-30 23:27:40.264",
      "started_max": "2025-09-30 23:58:26.908"
    },
    {
      "source_file_key": "202510-citibike-tripdata.zip",
      "rows": "4731900",
      "distinct_ride_ids": "4731900",
      "started_min": "2025-09-30 02:10:54.819",
      "started_max": "2025-10-31 23:58:16.325"
    },
    {
      "source_file_key": "202511-citibike-tripdata.zip",
      "rows": "3417052",
      "distinct_ride_ids": "3417052",
      "started_min": "2025-10-31 01:00:18.978",
      "started_max": "2025-11-30 23:57:47.774"
    },
    {
      "source_file_key": "202512-citibike-tripdata.zip",
      "rows": "2091620",
      "distinct_ride_ids": "2091620",
      "started_min": "2025-11-30 15:46:22.694",
      "started_max": "2025-12-31 23:57:28.999"
    },
    {
      "source_file_key": "202601-citibike-tripdata.zip",
      "rows": "1816391",
      "distinct_ride_ids": "1816391",
      "started_min": "2025-12-30 23:30:09.507",
      "started_max": "2026-01-31 23:56:50.315"
    }
  ],
  "rejects": [],
  "manifest": [
    {
      "month": "202501",
      "status": "completed",
      "file_key": "202501-citibike-tripdata.zip",
      "checksum_sha256": "ed3b0138903960afe50386ab44fec8bf52d69474b26f5c0af83e648094aea096",
      "bytes": "414213312",
      "rows_read": "2124475",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "2124475"
    },
    {
      "month": "202502",
      "status": "completed",
      "file_key": "202502-citibike-tripdata.zip",
      "checksum_sha256": "e4860762fb07561bbda251a20223d996e39c6c2c8bc2592896507bfbaa40ce78",
      "bytes": "396008433",
      "rows_read": "2031257",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "2031257"
    },
    {
      "month": "202503",
      "status": "completed",
      "file_key": "202503-citibike-tripdata.zip",
      "checksum_sha256": "f5cc8c5a4ed99e719a4a9d72014b3b206ab03c2b23ce05a2af4be6564444833f",
      "bytes": "618260276",
      "rows_read": "3168271",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "3168271"
    },
    {
      "month": "202504",
      "status": "completed",
      "file_key": "202504-citibike-tripdata.zip",
      "checksum_sha256": "5bda09477bb675e5a481a6f68acd367c2cae48e745a97011ca8a22c0073cece4",
      "bytes": "727249076",
      "rows_read": "3724596",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "3724596"
    },
    {
      "month": "202505",
      "status": "completed",
      "file_key": "202505-citibike-tripdata.zip",
      "checksum_sha256": "0f5edc46361c4581c614a19cb697271b6f04143f6648b56da8fc18f7310ab103",
      "bytes": "844446636",
      "rows_read": "4325553",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "4325553"
    },
    {
      "month": "202506",
      "status": "completed",
      "file_key": "202506-citibike-tripdata.zip",
      "checksum_sha256": "fdc15ebc98ddef6ffa6825a71616e2f704def8376590881f91b0109cd2d81f9b",
      "bytes": "929043436",
      "rows_read": "4759345",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "4759345"
    },
    {
      "month": "202507",
      "status": "completed",
      "file_key": "202507-citibike-tripdata.zip",
      "checksum_sha256": "5c336f33986e229203308f1e177f42d46bce3c6a3a85aac7d412cd7109b3253b",
      "bytes": "973875638",
      "rows_read": "4988053",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "4988053"
    },
    {
      "month": "202508",
      "status": "completed",
      "file_key": "202508-citibike-tripdata.zip",
      "checksum_sha256": "c15c6f1a3c3084fa03c012a20f88474907cca74119c31e58eb877b753dbc19b1",
      "bytes": "1000258641",
      "rows_read": "5121533",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "5121533"
    },
    {
      "month": "202509",
      "status": "completed",
      "file_key": "202509-citibike-tripdata.zip",
      "checksum_sha256": "b1721f1f9306daf2ca5aa358efe64c70da7c58cda82ae595692bb2d7cf2d5e46",
      "bytes": "1032411368",
      "rows_read": "5287447",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "5287447"
    },
    {
      "month": "202510",
      "status": "completed",
      "file_key": "202510-citibike-tripdata.zip",
      "checksum_sha256": "6a8ea3a973ed92caa6129795ff5e0cc758aa9d42c81af579b8f1ebf9f555a6be",
      "bytes": "924115389",
      "rows_read": "4731900",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "4731900"
    },
    {
      "month": "202511",
      "status": "completed",
      "file_key": "202511-citibike-tripdata.zip",
      "checksum_sha256": "279fc68f6ebc06dec938c7574f843707b2e86c590b3126050672938ad0a8345f",
      "bytes": "667113932",
      "rows_read": "3417052",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "3417052"
    },
    {
      "month": "202512",
      "status": "completed",
      "file_key": "202512-citibike-tripdata.zip",
      "checksum_sha256": "504448a1cd0b8a4630da880a861bc73bbd4d88fad59a66f18ec23a84e5747f45",
      "bytes": "407790791",
      "rows_read": "2091620",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "2091620"
    },
    {
      "month": "202601",
      "status": "completed",
      "file_key": "202601-citibike-tripdata.zip",
      "checksum_sha256": "8d579be9515f797739f0ff907576da44f25413bc677e1286a876696a71ae9755",
      "bytes": "353829139",
      "rows_read": "1816391",
      "rows_accepted": "0",
      "rows_rejected": "0",
      "duplicates_detected": "1816391"
    }
  ],
  "staging_statuses": {
    "ambiguous": 4124,
    "valid": 47583369
  }
}
```

## 5. Coverage and DST

Thirteen adjacent New York calendar-month windows use explicit UTC offsets. March changes from -05:00 to -04:00; November reverses that transition. The March 9 local day must contain 23 hours; November 2 must contain 25. The repeated fall-back pair stays uncertain with no demand or invented UTC. Unexplained empty source hours are uncertain/unexplained_source_gap and remain NULL. The verified January gap seed is unchanged; 18 unavailable hours and the two onset/resume partial hours are compared to the frozen dataset.

```json
{
  "coverage": {
    "window_count": 13,
    "first_start_utc": "2025-01-01 05:00:00+00:00",
    "last_end_utc": "2026-02-01 05:00:00+00:00",
    "expected_hours": 9504,
    "coverage_rows": 9504,
    "contiguous": true,
    "gap_count": 1,
    "gaps": [
      {
        "month": "202601",
        "gap_start": "2026-01-25 17:00:00+00:00",
        "gap_end": "2026-01-26 12:00:00+00:00",
        "gap_label": "source_outage_2026_01_25"
      }
    ],
    "status_counts": {
      "boundary": 312,
      "complete": 9170,
      "partial": 2,
      "unavailable": 18,
      "uncertain": 2
    }
  },
  "dst": {
    "spring_forward_local_day_hours": 23,
    "fall_back_local_day_hours": 25,
    "fall_back_uncertain_hours": 2,
    "fall_back_uncertain_statuses": [
      "uncertain",
      "uncertain"
    ],
    "fall_back_uncertain_reasons": [
      "dst_ambiguous_hour",
      "dst_ambiguous_hour"
    ]
  }
}
```

## 6. dbt and test results

Existing unit/forecasting suite: 49 passed. Isolated PostgreSQL integration suite: 15 passed, including bulk conflicts, rejects, reruns and atomic checkpoint rollback. Additional Phase 6B tests: 2 actual-mart SQL cases passed (missing hours, DST and outage handling) and 1 dataset export case passed (missing edge hours and t-2). The complete dbt build includes seeds, staging and marts; staging is never excluded. The following are actual finalization command results:

```json
{
  "backfill-complete": {
    "exit_code": 0,
    "seconds": 1573.43
  },
  "dbt-build": {
    "exit_code": 0,
    "seconds": 6819.46
  },
  "staging-idempotent-rerun": {
    "exit_code": 0,
    "seconds": 134.82
  },
  "dataset-build": {
    "exit_code": 0,
    "seconds": 2.68
  },
  "verification": {
    "exit_code": 0,
    "seconds": 387.02
  }
}
```

## 7. Existing-period regression

Verification compares October 2025–January 2026 targets, membership counts, coverage status/reason, eligibility and boundary flags to the frozen dataset. A pre/post fingerprint compares all 12,056,963 existing ride IDs, start/end UTC instants and timestamp statuses. Additional older months may populate September hours previously outside coverage; that does not authorize rewriting October–January.

## 8. Expanded dataset

```json
{
  "path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/processed/phase6b/forecast_dataset_2025_01_2026_01.csv",
  "sha256": "722300ddfbe0bca1c107192c9f333a94ec68c2d6c8275837aa44078b265ddb02",
  "rows": 9504,
  "columns": 38,
  "unique_hours": 9504,
  "start_utc": "2025-01-01 05:00:00+00:00",
  "end_utc": "2026-02-01 04:00:00+00:00",
  "coverage_counts": {
    "boundary": 312,
    "complete": 9170,
    "partial": 2,
    "unavailable": 18,
    "uncertain": 2
  },
  "missing_targets": 21,
  "in_demand_rows": 9483
}
```

The versioned CSV lives under `data/processed/phase6b/`. Coverage defines its continuous hourly spine, including missing edge hours. Every lag and rolling statistic is independently checked against the uncompressed target series. The latest permitted observation is t-2; missing and uncertain targets stay missing. Frozen Phase 5B file and dataset SHA-256 values must remain unchanged.

## 9. Runtime and disk consumption

```json
{
  "started_at_utc": "2026-10-27T15:11:20.834755+00:00",
  "finished_at_utc": "2026-10-27T21:24:05.680264+00:00",
  "elapsed_seconds": 22364.72,
  "storage_before": {
    "host_free_bytes": 83732348928,
    "database_bytes": 36779277335,
    "cache_bytes": 9288616067
  },
  "storage_after": {
    "host_free_bytes": 78781845504,
    "database_bytes": 40896945175,
    "cache_bytes": 9288616067
  },
  "reruns": {
    "202501": {
      "result": {
        "month": "202501",
        "archive_key": "202501-citibike-tripdata.zip",
        "archive_size": 414213312,
        "archive_path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/cache/202501-citibike-tripdata.zip",
        "checksum_sha256": "ed3b0138903960afe50386ab44fec8bf52d69474b26f5c0af83e648094aea096",
        "csv_members": [
          "202501-citibike-tripdata_1.csv",
          "202501-citibike-tripdata_2.csv",
          "202501-citibike-tripdata_3.csv"
        ],
        "rows_read": 2124475,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "duplicates_detected": 2124475,
        "elapsed_seconds": 239.05
      },
      "reconciliation": {
        "month": "202501",
        "checks": {
          "manifest_completed": true,
          "file_key_match": true,
          "checksum_match": true,
          "bytes_match": true,
          "rows_read_match": true,
          "row_accounting": true,
          "rejects_match": true,
          "raw_count_match": true,
          "raw_ids_unique": true,
          "raw_started_range_match": true
        },
        "manifest": {
          "id": 11,
          "status": "completed",
          "file_key": "202501-citibike-tripdata.zip",
          "checksum_sha256": "ed3b0138903960afe50386ab44fec8bf52d69474b26f5c0af83e648094aea096",
          "bytes": 414213312,
          "rows_read": 2124475,
          "rows_accepted": 0,
          "rows_rejected": 0,
          "duplicates_detected": 2124475,
          "error_message": null
        },
        "raw": {
          "rows": 2124475,
          "distinct_ride_ids": 2124475,
          "started_at_raw_min": "2024-12-30 23:41:25.635",
          "started_at_raw_max": "2025-01-31 23:58:14.634",
          "ingested_at_min": "2026-10-25 15:58:28.602969+00:00",
          "ingested_at_max": "2026-10-25 16:34:49.249194+00:00"
        },
        "database": {
          "raw_rows": 47587493,
          "raw_distinct_ride_ids": 47587493,
          "raw_duplicate_ride_ids": 0,
          "reject_rows": 0,
          "database_bytes": 36779318295,
          "raw_trips_bytes": 20858585088,
          "stg_trips_bytes": 15910150144
        },
        "host_free_bytes": 83676024832
      }
    },
    "202502": {
      "result": {
        "month": "202502",
        "archive_key": "202502-citibike-tripdata.zip",
        "archive_size": 396008433,
        "archive_path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/cache/202502-citibike-tripdata.zip",
        "checksum_sha256": "e4860762fb07561bbda251a20223d996e39c6c2c8bc2592896507bfbaa40ce78",
        "csv_members": [
          "202502-citibike-tripdata_1.csv",
          "202502-citibike-tripdata_2.csv",
          "202502-citibike-tripdata_3.csv"
        ],
        "rows_read": 2031257,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "duplicates_detected": 2031257,
        "elapsed_seconds": 228.32
      },
      "reconciliation": {
        "month": "202502",
        "checks": {
          "manifest_completed": true,
          "file_key_match": true,
          "checksum_match": true,
          "bytes_match": true,
          "rows_read_match": true,
          "row_accounting": true,
          "rejects_match": true,
          "raw_count_match": true,
          "raw_ids_unique": true,
          "raw_started_range_match": true
        },
        "manifest": {
          "id": 14,
          "status": "completed",
          "file_key": "202502-citibike-tripdata.zip",
          "checksum_sha256": "e4860762fb07561bbda251a20223d996e39c6c2c8bc2592896507bfbaa40ce78",
          "bytes": 396008433,
          "rows_read": 2031257,
          "rows_accepted": 0,
          "rows_rejected": 0,
          "duplicates_detected": 2031257,
          "error_message": null
        },
        "raw": {
          "rows": 2031257,
          "distinct_ride_ids": 2031257,
          "started_at_raw_min": "2025-01-31 09:26:50.490",
          "started_at_raw_max": "2025-02-28 23:58:02.884",
          "ingested_at_min": "2026-10-26 14:51:52.927117+00:00",
          "ingested_at_max": "2026-10-26 15:04:46.321044+00:00"
        },
        "database": {
          "raw_rows": 47587493,
          "raw_distinct_ride_ids": 47587493,
          "raw_duplicate_ride_ids": 0,
          "reject_rows": 0,
          "database_bytes": 36779326487,
          "raw_trips_bytes": 20858585088,
          "stg_trips_bytes": 15910150144
        },
        "host_free_bytes": 83686965248
      }
    },
    "202503": {
      "result": {
        "month": "202503",
        "archive_key": "202503-citibike-tripdata.zip",
        "archive_size": 618260276,
        "archive_path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/cache/202503-citibike-tripdata.zip",
        "checksum_sha256": "f5cc8c5a4ed99e719a4a9d72014b3b206ab03c2b23ce05a2af4be6564444833f",
        "csv_members": [
          "202503-citibike-tripdata_1.csv",
          "202503-citibike-tripdata_2.csv",
          "202503-citibike-tripdata_3.csv",
          "202503-citibike-tripdata_4.csv"
        ],
        "rows_read": 3168271,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "duplicates_detected": 3168271,
        "elapsed_seconds": 332.53
      },
      "reconciliation": {
        "month": "202503",
        "checks": {
          "manifest_completed": true,
          "file_key_match": true,
          "checksum_match": true,
          "bytes_match": true,
          "rows_read_match": true,
          "row_accounting": true,
          "rejects_match": true,
          "raw_count_match": true,
          "raw_ids_unique": true,
          "raw_started_range_match": true
        },
        "manifest": {
          "id": 15,
          "status": "completed",
          "file_key": "202503-citibike-tripdata.zip",
          "checksum_sha256": "f5cc8c5a4ed99e719a4a9d72014b3b206ab03c2b23ce05a2af4be6564444833f",
          "bytes": 618260276,
          "rows_read": 3168271,
          "rows_accepted": 0,
          "rows_rejected": 0,
          "duplicates_detected": 3168271,
          "error_message": null
        },
        "raw": {
          "rows": 3168271,
          "distinct_ride_ids": 3168271,
          "started_at_raw_min": "2025-02-28 08:43:52.291",
          "started_at_raw_max": "2025-03-31 23:56:56.495",
          "ingested_at_min": "2026-10-26 15:11:52.391270+00:00",
          "ingested_at_max": "2026-10-26 15:27:23.390795+00:00"
        },
        "database": {
          "raw_rows": 47587493,
          "raw_distinct_ride_ids": 47587493,
          "raw_duplicate_ride_ids": 0,
          "reject_rows": 0,
          "database_bytes": 36779326487,
          "raw_trips_bytes": 20858585088,
          "stg_trips_bytes": 15910150144
        },
        "host_free_bytes": 83696214016
      }
    },
    "202504": {
      "result": {
        "month": "202504",
        "archive_key": "202504-citibike-tripdata.zip",
        "archive_size": 727249076,
        "archive_path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/cache/202504-citibike-tripdata.zip",
        "checksum_sha256": "5bda09477bb675e5a481a6f68acd367c2cae48e745a97011ca8a22c0073cece4",
        "csv_members": [
          "202504-citibike-tripdata_1.csv",
          "202504-citibike-tripdata_2.csv",
          "202504-citibike-tripdata_3.csv",
          "202504-citibike-tripdata_4.csv"
        ],
        "rows_read": 3724596,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "duplicates_detected": 3724596,
        "elapsed_seconds": 222.52
      },
      "reconciliation": {
        "month": "202504",
        "checks": {
          "manifest_completed": true,
          "file_key_match": true,
          "checksum_match": true,
          "bytes_match": true,
          "rows_read_match": true,
          "row_accounting": true,
          "rejects_match": true,
          "raw_count_match": true,
          "raw_ids_unique": true,
          "raw_started_range_match": true
        },
        "manifest": {
          "id": 17,
          "status": "completed",
          "file_key": "202504-citibike-tripdata.zip",
          "checksum_sha256": "5bda09477bb675e5a481a6f68acd367c2cae48e745a97011ca8a22c0073cece4",
          "bytes": 727249076,
          "rows_read": 3724596,
          "rows_accepted": 0,
          "rows_rejected": 0,
          "duplicates_detected": 3724596,
          "error_message": null
        },
        "raw": {
          "rows": 3724596,
          "distinct_ride_ids": 3724596,
          "started_at_raw_min": "2025-03-31 02:22:50.612",
          "started_at_raw_max": "2025-04-30 23:58:20.015",
          "ingested_at_min": "2026-10-26 15:52:10.833347+00:00",
          "ingested_at_max": "2026-10-26 16:42:30.543249+00:00"
        },
        "database": {
          "raw_rows": 47587493,
          "raw_distinct_ride_ids": 47587493,
          "raw_duplicate_ride_ids": 0,
          "reject_rows": 0,
          "database_bytes": 36779318295,
          "raw_trips_bytes": 20858585088,
          "stg_trips_bytes": 15910150144
        },
        "host_free_bytes": 82568486912
      }
    },
    "202505": {
      "result": {
        "month": "202505",
        "archive_key": "202505-citibike-tripdata.zip",
        "archive_size": 844446636,
        "archive_path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/cache/202505-citibike-tripdata.zip",
        "checksum_sha256": "0f5edc46361c4581c614a19cb697271b6f04143f6648b56da8fc18f7310ab103",
        "csv_members": [
          "202505-citibike-tripdata_1.csv",
          "202505-citibike-tripdata_2.csv",
          "202505-citibike-tripdata_3.csv",
          "202505-citibike-tripdata_4.csv",
          "202505-citibike-tripdata_5.csv"
        ],
        "rows_read": 4325553,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "duplicates_detected": 4325553,
        "elapsed_seconds": 201.82
      },
      "reconciliation": {
        "month": "202505",
        "checks": {
          "manifest_completed": true,
          "file_key_match": true,
          "checksum_match": true,
          "bytes_match": true,
          "rows_read_match": true,
          "row_accounting": true,
          "rejects_match": true,
          "raw_count_match": true,
          "raw_ids_unique": true,
          "raw_started_range_match": true
        },
        "manifest": {
          "id": 18,
          "status": "completed",
          "file_key": "202505-citibike-tripdata.zip",
          "checksum_sha256": "0f5edc46361c4581c614a19cb697271b6f04143f6648b56da8fc18f7310ab103",
          "bytes": 844446636,
          "rows_read": 4325553,
          "rows_accepted": 0,
          "rows_rejected": 0,
          "duplicates_detected": 4325553,
          "error_message": null
        },
        "raw": {
          "rows": 4325553,
          "distinct_ride_ids": 4325553,
          "started_at_raw_min": "2025-04-29 23:02:37.671",
          "started_at_raw_max": "2025-05-31 23:58:06.793",
          "ingested_at_min": "2026-10-27 12:58:52.223245+00:00",
          "ingested_at_max": "2026-10-27 13:23:49.715730+00:00"
        },
        "database": {
          "raw_rows": 47587493,
          "raw_distinct_ride_ids": 47587493,
          "raw_duplicate_ride_ids": 0,
          "reject_rows": 0,
          "database_bytes": 36779334679,
          "raw_trips_bytes": 20858585088,
          "stg_trips_bytes": 15910150144
        },
        "host_free_bytes": 82612473856
      }
    },
    "202506": {
      "result": {
        "month": "202506",
        "archive_key": "202506-citibike-tripdata.zip",
        "archive_size": 929043436,
        "archive_path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/cache/202506-citibike-tripdata.zip",
        "checksum_sha256": "fdc15ebc98ddef6ffa6825a71616e2f704def8376590881f91b0109cd2d81f9b",
        "csv_members": [
          "202506-citibike-tripdata_1.csv",
          "202506-citibike-tripdata_2.csv",
          "202506-citibike-tripdata_3.csv",
          "202506-citibike-tripdata_4.csv",
          "202506-citibike-tripdata_5.csv"
        ],
        "rows_read": 4759345,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "duplicates_detected": 4759345,
        "elapsed_seconds": 198.31
      },
      "reconciliation": {
        "month": "202506",
        "checks": {
          "manifest_completed": true,
          "file_key_match": true,
          "checksum_match": true,
          "bytes_match": true,
          "rows_read_match": true,
          "row_accounting": true,
          "rejects_match": true,
          "raw_count_match": true,
          "raw_ids_unique": true,
          "raw_started_range_match": true
        },
        "manifest": {
          "id": 19,
          "status": "completed",
          "file_key": "202506-citibike-tripdata.zip",
          "checksum_sha256": "fdc15ebc98ddef6ffa6825a71616e2f704def8376590881f91b0109cd2d81f9b",
          "bytes": 929043436,
          "rows_read": 4759345,
          "rows_accepted": 0,
          "rows_rejected": 0,
          "duplicates_detected": 4759345,
          "error_message": null
        },
        "raw": {
          "rows": 4759345,
          "distinct_ride_ids": 4759345,
          "started_at_raw_min": "2025-05-30 23:47:29.703",
          "started_at_raw_max": "2025-06-30 23:58:25.278",
          "ingested_at_min": "2026-10-27 13:41:53.473521+00:00",
          "ingested_at_max": "2026-10-27 14:09:36.913411+00:00"
        },
        "database": {
          "raw_rows": 47587493,
          "raw_distinct_ride_ids": 47587493,
          "raw_duplicate_ride_ids": 0,
          "reject_rows": 0,
          "database_bytes": 36779342871,
          "raw_trips_bytes": 20858585088,
          "stg_trips_bytes": 15910150144
        },
        "host_free_bytes": 82608750592
      }
    },
    "202507": {
      "result": {
        "month": "202507",
        "archive_key": "202507-citibike-tripdata.zip",
        "archive_size": 973875638,
        "archive_path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/cache/202507-citibike-tripdata.zip",
        "checksum_sha256": "5c336f33986e229203308f1e177f42d46bce3c6a3a85aac7d412cd7109b3253b",
        "csv_members": [
          "202507-citibike-tripdata_1.csv",
          "202507-citibike-tripdata_2.csv",
          "202507-citibike-tripdata_3.csv",
          "202507-citibike-tripdata_4.csv",
          "202507-citibike-tripdata_5.csv"
        ],
        "rows_read": 4988053,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "duplicates_detected": 4988053,
        "elapsed_seconds": 215.77
      },
      "reconciliation": {
        "month": "202507",
        "checks": {
          "manifest_completed": true,
          "file_key_match": true,
          "checksum_match": true,
          "bytes_match": true,
          "rows_read_match": true,
          "row_accounting": true,
          "rejects_match": true,
          "raw_count_match": true,
          "raw_ids_unique": true,
          "raw_started_range_match": true
        },
        "manifest": {
          "id": 20,
          "status": "completed",
          "file_key": "202507-citibike-tripdata.zip",
          "checksum_sha256": "5c336f33986e229203308f1e177f42d46bce3c6a3a85aac7d412cd7109b3253b",
          "bytes": 973875638,
          "rows_read": 4988053,
          "rows_accepted": 0,
          "rows_rejected": 0,
          "duplicates_detected": 4988053,
          "error_message": null
        },
        "raw": {
          "rows": 4988053,
          "distinct_ride_ids": 4988053,
          "started_at_raw_min": "2025-06-29 23:16:34.475",
          "started_at_raw_max": "2025-07-31 23:57:27.342",
          "ingested_at_min": "2026-10-27 14:28:19.941019+00:00",
          "ingested_at_max": "2026-10-27 15:01:03.369014+00:00"
        },
        "database": {
          "raw_rows": 47587493,
          "raw_distinct_ride_ids": 47587493,
          "raw_duplicate_ride_ids": 0,
          "reject_rows": 0,
          "database_bytes": 36779375639,
          "raw_trips_bytes": 20858585088,
          "stg_trips_bytes": 15910150144
        },
        "host_free_bytes": 82650607616
      }
    },
    "202508": {
      "result": {
        "month": "202508",
        "archive_key": "202508-citibike-tripdata.zip",
        "archive_size": 1000258641,
        "archive_path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/cache/202508-citibike-tripdata.zip",
        "checksum_sha256": "c15c6f1a3c3084fa03c012a20f88474907cca74119c31e58eb877b753dbc19b1",
        "csv_members": [
          "202508-citibike-tripdata_1.csv",
          "202508-citibike-tripdata_2.csv",
          "202508-citibike-tripdata_3.csv",
          "202508-citibike-tripdata_4.csv",
          "202508-citibike-tripdata_5.csv",
          "202508-citibike-tripdata_6.csv"
        ],
        "rows_read": 5121533,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "duplicates_detected": 5121533,
        "elapsed_seconds": 233.43
      },
      "reconciliation": {
        "month": "202508",
        "checks": {
          "manifest_completed": true,
          "file_key_match": true,
          "checksum_match": true,
          "bytes_match": true,
          "rows_read_match": true,
          "row_accounting": true,
          "rejects_match": true,
          "raw_count_match": true,
          "raw_ids_unique": true,
          "raw_started_range_match": true
        },
        "manifest": {
          "id": 21,
          "status": "completed",
          "file_key": "202508-citibike-tripdata.zip",
          "checksum_sha256": "c15c6f1a3c3084fa03c012a20f88474907cca74119c31e58eb877b753dbc19b1",
          "bytes": 1000258641,
          "rows_read": 5121533,
          "rows_accepted": 0,
          "rows_rejected": 0,
          "duplicates_detected": 5121533,
          "error_message": null
        },
        "raw": {
          "rows": 5121533,
          "distinct_ride_ids": 5121533,
          "started_at_raw_min": "2025-07-30 23:00:23.818",
          "started_at_raw_max": "2025-08-31 23:58:17.885",
          "ingested_at_min": "2026-10-27 15:28:08.460335+00:00",
          "ingested_at_max": "2026-10-27 16:30:10.249761+00:00"
        },
        "database": {
          "raw_rows": 47587493,
          "raw_distinct_ride_ids": 47587493,
          "raw_duplicate_ride_ids": 0,
          "reject_rows": 0,
          "database_bytes": 36779359255,
          "raw_trips_bytes": 20858585088,
          "stg_trips_bytes": 15910150144
        },
        "host_free_bytes": 82584997888
      }
    },
    "202509": {
      "result": {
        "month": "202509",
        "archive_key": "202509-citibike-tripdata.zip",
        "archive_size": 1032411368,
        "archive_path": "/Users/ihebsayah/Documents/my portfolio/my data pipeline/data/cache/202509-citibike-tripdata.zip",
        "checksum_sha256": "b1721f1f9306daf2ca5aa358efe64c70da7c58cda82ae595692bb2d7cf2d5e46",
        "csv_members": [
          "202509-citibike-tripdata_1.csv",
          "202509-citibike-tripdata_2.csv",
          "202509-citibike-tripdata_3.csv",
          "202509-citibike-tripdata_4.csv",
          "202509-citibike-tripdata_5.csv",
          "202509-citibike-tripdata_6.csv"
        ],
        "rows_read": 5287447,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "duplicates_detected": 5287447,
        "elapsed_seconds": 197.1
      },
      "reconciliation": {
        "month": "202509",
        "checks": {
          "manifest_completed": true,
          "file_key_match": true,
          "checksum_match": true,
          "bytes_match": true,
          "rows_read_match": true,
          "row_accounting": true,
          "rejects_match": true,
          "raw_count_match": true,
          "raw_ids_unique": true,
          "raw_started_range_match": true
        },
        "manifest": {
          "id": 22,
          "status": "completed",
          "file_key": "202509-citibike-tripdata.zip",
          "checksum_sha256": "b1721f1f9306daf2ca5aa358efe64c70da7c58cda82ae595692bb2d7cf2d5e46",
          "bytes": 1032411368,
          "rows_read": 5287447,
          "rows_accepted": 0,
          "rows_rejected": 0,
          "duplicates_detected": 5287447,
          "error_message": null
        },
        "raw": {
          "rows": 5287447,
          "distinct_ride_ids": 5287447,
          "started_at_raw_min": "2025-08-30 23:27:40.264",
          "started_at_raw_max": "2025-09-30 23:58:26.908",
          "ingested_at_min": "2026-10-27 17:00:52.034544+00:00",
          "ingested_at_max": "2026-10-27 17:52:03.526053+00:00"
        },
        "database": {
          "raw_rows": 47587493,
          "raw_distinct_ride_ids": 47587493,
          "raw_duplicate_ride_ids": 0,
          "reject_rows": 0,
          "database_bytes": 36779351063,
          "raw_trips_bytes": 20858585088,
          "stg_trips_bytes": 15910150144
        },
        "host_free_bytes": 82593075200
      }
    }
  }
}
```

Initial ingestion timings/storage snapshots retained in `backfill.json` cover the surviving recorded attempts. Earlier overwritten telemetry cannot be reconstructed as exact first-load runtime. Finalization durations include auditing, reruns and builds; they are not an exact comparison to the first-load-only estimate.

## 10. Proposed Phase 6C protocol — planning only

Use New York local month boundaries converted to UTC, with half-open intervals. Propose train January–August 2025, validation September–October 2025, and retrospective test November 2025–January 2026. Tune only on validation; use expanding-window folds within training for stability. Evaluate only complete targets for the primary population; report boundary/partial sensitivity separately. Keep missing spine rows, t-2 feature availability and past-only feature histories across split boundaries. No model fitting, selection or metric inspection is part of this phase.

All of this is retrospective backtesting. The November–January period has already been inspected in earlier phases and is not a pristine test set. Adding older months does not erase that exposure. A genuinely future holdout must be registered before its observations occur: propose November 2026–January 2027, with a frozen protocol/model before November 1, 2026 04:00 UTC. Any training extension needed to reach that cutoff requires a separately approved ingestion phase. Do not inspect that holdout's model metrics until its end and the preregistered single evaluation.

## 11. Limitations

Ambiguous source wall times cannot be resolved without additional evidence. Month-end boundary hours remain truncated. Immutable-raw incremental staging does not propagate raw edits; semantic changes require full refresh. Initial pre-expansion runtime telemetry is incomplete. Database sizes include indexes and existing bloat; host storage also includes the archive cache and unrelated use.

## 12. Readiness

**PASS**. Current step: complete. 

```json
{
  "coverage_window_count": true,
  "timezone_correct_calendar_month_windows": true,
  "coverage_contiguous": true,
  "coverage_hours_match_spine": true,
  "january_outage_exact": true,
  "spring_forward_day_23_hours": true,
  "fall_back_day_25_hours": true,
  "fall_back_two_uncertain_hours": true,
  "no_uncertain_in_demand_mart": true,
  "no_unavailable_demand": true,
  "old_period_row_count": true,
  "old_period_demand_and_coverage_unchanged": true,
  "dataset_continuous_hourly_spine": true,
  "dataset_rows_match_coverage": true,
  "dataset_targets_and_eligibility_match_marts": true,
  "dataset_unique_hours": true,
  "dataset_range_matches_coverage": true,
  "dataset_hash_matches_metadata": true,
  "dataset_lag_1_absent": true,
  "dataset_unsafe_rollups_absent": true,
  "target_missing_iff_not_eligible": true,
  "no_unverified_target_fill": true,
  "lag_2_matches_t_minus_2": true,
  "lag_24_exact": true,
  "lag_168_exact": true,
  "roll2_mean_24_exact": true,
  "cross_archive_duplicate_ids_zero": true,
  "staging_rows_equal_raw_rows": true,
  "staging_is_table": true,
  "staging_has_three_indexes": true,
  "no_invented_or_invalid_start_utc": true,
  "existing_period_timestamp_fingerprint_unchanged": true,
  "all_expected_source_file_keys": true,
  "no_synthetic_test_rows": true,
  "202501_source_reconciled": true,
  "202501_manifest_reconciled": true,
  "202502_source_reconciled": true,
  "202502_manifest_reconciled": true,
  "202503_source_reconciled": true,
  "202503_manifest_reconciled": true,
  "202504_source_reconciled": true,
  "202504_manifest_reconciled": true,
  "202505_source_reconciled": true,
  "202505_manifest_reconciled": true,
  "202506_source_reconciled": true,
  "202506_manifest_reconciled": true,
  "202507_source_reconciled": true,
  "202507_manifest_reconciled": true,
  "202508_source_reconciled": true,
  "202508_manifest_reconciled": true,
  "202509_source_reconciled": true,
  "202509_manifest_reconciled": true,
  "all_history_features_obey_t_minus_2": true,
  "frozen_phase5b_artifacts_unchanged": true,
  "frozen_phase5b_dataset_unchanged": true
}
```

Stop at Phase 6B. Architectural review is required before any new model training.
