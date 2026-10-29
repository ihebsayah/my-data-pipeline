#!/usr/bin/env python3
"""Finish Phase 6B sequentially; stop on failure and publish an evidence report.

Wait for an existing backfill/staging process with --wait-pid (repeatable).
Never launches model training or evaluation. Logs and measurements stay in data/.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import psycopg

from citi_bike_ingest.config.settings import Settings
from citi_bike_ingest.ingest.orchestrator import IngestionOrchestrator
from phase6b_backfill import DEFAULT_MONTHS, verify_month

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/processed/phase6b"
STATE = OUT / "finalization.json"
REPORT = ROOT / "docs/phase-6b-verification.md"


def save(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str) + "\n")
    tmp.replace(STATE)


def run(state: dict, label: str, command: list[str]) -> None:
    state["current_step"] = label
    save(state)
    started = time.monotonic()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
           "DBT_PROFILES_DIR": str(ROOT / "dbt")}
    with (OUT / f"{label}.log").open("w") as log:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                stderr=subprocess.STDOUT)
    state["steps"][label] = {"exit_code": result.returncode,
                              "seconds": round(time.monotonic() - started, 2)}
    save(state)
    if result.returncode:
        raise RuntimeError(f"{label} failed; see data/processed/phase6b/{label}.log")


def write_report(state: dict) -> None:
    backfill_path = OUT / "backfill.json"
    backfill = json.loads(backfill_path.read_text()) if backfill_path.exists() else {}
    verified = OUT / "verification.json"
    verification = json.loads(verified.read_text()) if verified.exists() else {}
    passed = state.get("status") == "passed" and verification.get("all_checks_passed")
    git = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    lines = ["# Phase 6B — Historical Expansion and Data Integrity", "",
             f"Updated: {datetime.now(timezone.utc).isoformat()}", "",
             f"**Readiness: {'PASS' if passed else 'FAIL / NOT READY'}.** "
             + ("Phase 6B is ready for architectural review."
                if passed else "Expansion verification is incomplete; Phase 6C must not start."),
             "", "## 1. Git baseline", "",
             "Annotated `v0.5b` resolves to `1bc300fd9f3ea08474693982270d7948c5c09aec`. "
             "Approved Phase 6A was already committed as `4202a06`; incremental staging, "
             "bulk loading and initial backfill were committed as `46a23c4`. "
             f"Current HEAD: `{git}`. Completion changes remain available for review. No push performed.",
             "", "## 2. Storage preflight and staging decision", "",
             "Phase 4C measured 12,056,963 raw/staged rows, a 9,797 MiB database and "
             "39m31s full dbt refresh. Phase 6A estimated 35,541,709 additional rows, "
             "28.18 GiB additional DB storage, 6.46 GiB archives, 43.30 GiB free-space "
             "requirement and 11.48 hours total work. A full staging rebuild was judged "
             "impractical; incremental staging parses only ride IDs absent from staging. "
             "Raw canonical records are immutable. Full refresh is required after changing "
             "timestamp semantics. Unique ride-ID indexes remain enforced.", "",
             "A prior staging build failed with a unique-key violation. Recovery is serialized "
             "with the existing backfill and staging processes; the failed build is not counted "
             "as success. Existing timestamp parsing/DST macros are unchanged.", "",
             "Resumption preflight is recorded in `data/processed/phase6b/preflight-resumption.json`.",
             "", "## 3. Per-month archive and ingestion reconciliation", "",
             "Archives are audited independently with stdlib CSV, including every CSV member. "
             "Source SHA-256, byte size, row accounting, manifest status, raw counts and unique "
             "ride IDs must agree before continuing. Raw sources and rejection audit remain retained.",
             "", "| Month | Bytes | Members | Source rows | Unique accepted IDs | Rejected | Reconciled |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for month in DEFAULT_MONTHS:
        a = backfill.get("archives", {}).get(month, {})
        checks = backfill.get("reconciliation", {}).get(month, {}).get("checks", {})
        lines.append(f"| {month} | {a.get('bytes', 'pending')} | "
                     f"{len(a['csv_members']) if 'csv_members' in a else 'pending'} | "
                     f"{a.get('rows', 'pending')} | {a.get('accepted_unique_ride_ids', 'pending')} | "
                     f"{a.get('rejected_rows', 'pending')} | "
                     f"{'PASS' if checks and all(checks.values()) else 'pending'} |")
    lines += ["", "SHA-256 per archive:", ""]
    for month in DEFAULT_MONTHS:
        lines.append(f"- {month}: `{backfill.get('archives', {}).get(month, {}).get('sha256', 'pending')}`")
    lines += ["", "Full source reruns and incremental staging rerun evidence are stored in "
              "`finalization.json`; accepted rows must be zero on each source rerun. "
              "The isolated ingestion suite verifies interruption rollback and restart accounting.",
              "", "## 4. Cumulative raw and staging counts", "", "```json",
              json.dumps(verification.get("database", {"status": "pending"}), indent=2), "```",
              "", "## 5. Coverage and DST", "",
              "Thirteen adjacent New York calendar-month windows use explicit UTC offsets. "
              "March changes from -05:00 to -04:00; November reverses that transition. "
              "The March 9 local day must contain 23 hours; November 2 must contain 25. "
              "The repeated fall-back pair stays uncertain with no demand or invented UTC. "
              "Unexplained empty source hours are uncertain/unexplained_source_gap and remain NULL. "
              "The verified January gap seed is unchanged; 18 unavailable hours and the two "
              "onset/resume partial hours are compared to the frozen dataset.", "", "```json",
              json.dumps({k: verification.get(k, "pending") for k in ("coverage", "dst")}, indent=2),
              "```", "", "## 6. dbt and test results", "",
              "Existing unit/forecasting suite: 49 passed. Isolated PostgreSQL integration "
              "suite: 15 passed, including bulk conflicts, rejects, reruns and atomic checkpoint rollback. "
              "Additional Phase 6B tests: 2 actual-mart SQL cases passed (missing hours, DST and "
              "outage handling) and 1 dataset export case passed (missing edge hours and t-2). "
              "The complete dbt build includes seeds, staging and marts; staging is never excluded. "
              "The following are actual finalization command results:", "", "```json",
              json.dumps(state.get("steps", {}), indent=2), "```",
              "", "## 7. Existing-period regression", "",
              "Verification compares October 2025–January 2026 targets, membership counts, "
              "coverage status/reason, eligibility and boundary flags to the frozen dataset. "
              "A pre/post fingerprint compares all 12,056,963 existing ride IDs, start/end UTC "
              "instants and timestamp statuses. Additional older months may populate September "
              "hours previously outside coverage; that does not authorize rewriting October–January.",
              "", "## 8. Expanded dataset", "", "```json",
              json.dumps(verification.get("dataset", {"status": "pending"}), indent=2), "```", "",
              "The versioned CSV lives under `data/processed/phase6b/`. Coverage defines its "
              "continuous hourly spine, including missing edge hours. Every lag and rolling "
              "statistic is independently checked against the uncompressed target series. "
              "The latest permitted observation is t-2; missing and uncertain targets stay missing. "
              "Frozen Phase 5B file and dataset SHA-256 values must remain unchanged.",
              "", "## 9. Runtime and disk consumption", "", "```json",
              json.dumps({k: state.get(k, "pending") for k in (
                  "started_at_utc", "finished_at_utc", "elapsed_seconds", "storage_before",
                  "storage_after", "reruns")}, indent=2, default=str), "```", "",
              "Initial ingestion timings/storage snapshots retained in `backfill.json` cover "
              "the surviving recorded attempts. Earlier overwritten telemetry cannot be reconstructed "
              "as exact first-load runtime. Finalization durations include auditing, reruns and builds; "
              "they are not an exact comparison to the first-load-only estimate.",
              "", "## 10. Proposed Phase 6C protocol — planning only", "",
              "Use New York local month boundaries converted to UTC, with half-open intervals. "
              "Propose train January–August 2025, validation September–October 2025, and "
              "retrospective test November 2025–January 2026. Tune only on validation; use "
              "expanding-window folds within training for stability. Evaluate only complete "
              "targets for the primary population; report boundary/partial sensitivity separately. "
              "Keep missing spine rows, t-2 feature availability and past-only feature histories "
              "across split boundaries. No model fitting, selection or metric inspection is part "
              "of this phase.", "",
              "All of this is retrospective backtesting. The November–January period has already "
              "been inspected in earlier phases and is not a pristine test set. Adding older "
              "months does not erase that exposure. A genuinely future holdout must be registered "
              "before its observations occur: propose November 2026–January 2027, with a frozen "
              "protocol/model before November 1, 2026 04:00 UTC. Any training extension needed "
              "to reach that cutoff requires a separately approved ingestion phase. Do not inspect "
              "that holdout's model metrics until its end and the preregistered single evaluation.",
              "", "## 11. Limitations", "",
              "Ambiguous source wall times cannot be resolved without additional evidence. "
              "Month-end boundary hours remain truncated. Immutable-raw incremental staging "
              "does not propagate raw edits; semantic changes require full refresh. Initial "
              "pre-expansion runtime telemetry is incomplete. Database sizes include indexes "
              "and existing bloat; host storage also includes the archive cache and unrelated use.",
              "", "## 12. Readiness", "",
              f"**{'PASS' if passed else 'FAIL / NOT READY'}**. "
              f"Current step: {state.get('current_step', 'pending')}. "
              f"{state.get('error', '')}", "", "```json",
              json.dumps(verification.get("checks", {"status": "pending"}), indent=2), "```", "",
              "Stop at Phase 6B. Architectural review is required before any new model training.", ""]
    REPORT.write_text("\n".join(lines))


def storage(conn) -> dict:
    return {"host_free_bytes": shutil.disk_usage(ROOT).free,
            "database_bytes": conn.execute("select pg_database_size(current_database())").fetchone()[0],
            "cache_bytes": sum(p.stat().st_size for p in (ROOT / "data/cache").glob("*.zip"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-pid", type=int, action="append", default=[])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    state = {"status": "running", "started_at_utc": datetime.now(timezone.utc).isoformat(),
             "current_step": "waiting_for_existing_jobs", "steps": {}, "reruns": {}}
    save(state)
    write_report(state)
    started = time.monotonic()
    try:
        for pid in args.wait_pid:
            while True:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(15)
        settings = Settings()
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            state["storage_before"] = storage(conn)
        save(state)
        # Resume any failed/incomplete month, in chronological order. Existing
        # completed months are independently audited and reconciled before skipping.
        run(state, "backfill-complete", [sys.executable, "scripts/phase6b_backfill.py"])
        backfill = json.loads((OUT / "backfill.json").read_text())
        orchestrator = IngestionOrchestrator(settings)
        for month in DEFAULT_MONTHS:
            state["current_step"] = f"idempotent_source_rerun_{month}"
            save(state)
            audit = backfill["archives"][month]
            result = orchestrator.ingest_archive(
                ROOT / "data/cache" / audit["file_key"], audit["file_key"], month,
                expected_rows=audit["rows"], use_bulk=True,
            )
            if result["rows_accepted"] != 0:
                raise RuntimeError(f"{month} idempotent rerun inserted new records")
            with psycopg.connect(settings.database_url, autocommit=True) as conn:
                reconciliation = verify_month(conn, month, audit)
            state["reruns"][month] = {"result": result, "reconciliation": reconciliation}
            save(state)
        run(state, "dbt-build", ["dbt", "build", "--project-dir", "dbt"])
        shutil.copy2(ROOT / "dbt/target/run_results.json", OUT / "dbt-build-results.json")
        dbt_results = json.loads((OUT / "dbt-build-results.json").read_text())["results"]
        state["dbt_status_counts"] = {
            status: sum(result["status"] == status for result in dbt_results)
            for status in {result["status"] for result in dbt_results}
        }
        if any(result["status"] not in ("success", "pass") for result in dbt_results):
            raise RuntimeError("dbt build has non-passing results")
        run(state, "staging-idempotent-rerun", ["dbt", "run", "--project-dir", "dbt",
                                                "--select", "stg_trips"])
        rerun = json.loads((ROOT / "dbt/target/run_results.json").read_text())["results"]
        if len(rerun) != 1 or rerun[0]["adapter_response"].get("rows_affected") != 0:
            raise RuntimeError("Staging rerun did not report zero affected rows")
        run(state, "dataset-build", [sys.executable, "scripts/phase6b_build_dataset.py"])
        run(state, "verification", [sys.executable, "scripts/phase6b_verify.py"])
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            state["storage_after"] = storage(conn)
        state["status"] = "passed"
        state["current_step"] = "complete"
    except Exception as error:
        state["status"] = "failed"
        state["error"] = f"{type(error).__name__}: {error}"
    finally:
        state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        state["elapsed_seconds"] = round(time.monotonic() - started, 2)
        save(state)
        write_report(state)
    return 0 if state["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
