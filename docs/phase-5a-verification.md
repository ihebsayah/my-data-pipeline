# Phase 5A Verification — Forecasting Contract, Leakage-Safe Features, and Baselines

**Date:** 2026-10-24  
**Status:** Phase 5A complete (awaiting review). No weather enrichment, complex ML models, MLflow, FastAPI, dashboard, or Airflow added.

## 1) Forecasting contract

| Field | Value | Notes |
|---|---|---|
| **Target** | `rides_total` (citywide hourly Citi Bike trip starts) | From `marts.fct_hourly_demand` |
| **Horizon** | 1 hour ahead | One-hour-ahead forecast |
| **Prediction timestamp** | Beginning of the target UTC hour (`hour_utc` of target hour) | As defined in contract |
| **Available inputs** | Information known strictly before the prediction timestamp | Enforced by lag/rolling construction (shifted) |
| **Canonical time index** | `hour_utc` | Continuous UTC hourly spine |
| **Calendar features** | Derived from `America/New_York` local time | hour, dow, weekend, month + cyclical encodings |
| **Previous hour observed demand availability** | **Assumed available** at prediction time (configurable) | **Ingestion latency unknown** - stated as assumption in `src/forecasting/config.py` and `contract.py`. Configurable via `ForecastingConfig.previous_hour_available_at_prediction` / `ingestion_latency_assumption_note`. |

**Contract file:** `src/forecasting/contract.py` (also saved in metadata/results).

## 2) Coverage eligibility rules

Following Phase 4C guidance (Section 8): training-hour selection uses eligibility based on `fct_hourly_coverage.in_demand_mart` and status rules.

| coverage_status | Treatment | Rationale |
|---|---|---|
| `complete` | **Eligible** (primary evaluation) | Fully verified |
| `boundary` | **Eligible** (primary evaluation) | End-of-window hours; truncated by source (end-keyed archives). Flagged as truncated - included in eligibility but noted. |
| `partial` | **Eligible** if in demand mart (gap_resume) | Gap onset excluded (4 trips) per 4C; gap resume (132 trips) retained in demand mart. We treat per-coverage-status as per mart rules. |
| `unavailable` | **Excluded** | Verified outage (18h) - hours absent from demand mart |
| `uncertain` | **Excluded** | DST ambiguous hours (2h, 2025-11-02 fall-back) - cannot attribute verified count; NULL demand, never zeroed. |

**Implementation:** `src/forecasting/config.py` sets `eligible_coverage_statuses = ("complete", "boundary", "partial")` and excludes uncertain/unavailable. Evaluation uses identical eligible target timestamps when comparing baselines.

From actual data:
- In demand mart (eligible by mart): 2932 hours
- Coverage breakdown: complete 2835, boundary 96, partial 2, unavailable 18, uncertain 2

## 3) Feature definitions and availability assumptions

### Feature building module
- **Module:** `src/forecasting/features.py`, `src/forecasting/dataset.py`
- **Reproducible build:** `scripts/build_forecast_dataset.py` (deterministic commands provided)

### Spine and continuity
- **Continuous UTC hourly spine** built across full range via `pd.date_range(freq='h')`. 
- **Missing/excluded hours remain missing** - merged with `how='left'`, no forward/backward fill. No shifting of past missing hours to create artificial lag values.
- **Current-hour demand never used as predictor.** All lags/rolls use `.shift()`.

### Calendar features (from America/New_York)
- `hour_local`, `day_of_week_local` (Monday=0), `is_weekend_local` (Sat/Sun), `month`, `day_of_month_local`, `is_month_start`, `is_month_end`, `isodow_local` (Mon=1..Sun=7)
- Cyclical encodings: `hour_sin/cos`, `dow_sin/cos`, `month_sin/cos`

### Historical demand features (lags on continuous UTC spine)
- Lags: `lag_1`, `lag_24`, `lag_48`, `lag_72`, `lag_168` (computed via `shift(lag)`)
- Rolling statistics (past-only, exclude target): `roll_mean_{w}`, `roll_std_{w}`, `roll_min_{w}`, `roll_max_{w}` for windows `(24, 168)`, computed as `shift(1).rolling(window, min_periods=window)`

**Availability assumptions:**
- Lags require history to exist on the continuous spine; if previous hours missing/unavailable, lag values become NaN.
- Rolling stats require full window of past observations (`min_periods=window`) - otherwise NaN.
- Ingestion latency assumed unknown; for naive baselines we assume previous hour observed demand available at prediction time (configurable).

## 4) Train/validation/test boundaries

Fixed chronological splits (no shuffle). Windows chosen based on actual coverage.

From `data/processed/baseline_results.json`:
| Split | Count | Start (UTC) | End (UTC) |
|---|---|---|---|
| **Train** | 2067 | 2025-10-01 04:00:00+00:00 | 2025-12-26 06:00:00+00:00 |
| **Validation** | 443 | 2025-12-26 07:00:00+00:00 | 2026-01-13 17:00:00+00:00 |
| **Test** | 443 | 2026-01-13 18:00:00+00:00 | 2026-02-01 04:00:00+00:00 |

Splits are contiguous, non-overlapping, and chronological. Identical eligible target timestamps used for baseline comparisons.

## 5) Baseline results and evaluated sample counts

All evaluations require prediction available AND target available; for missing lag values predictions are marked unavailable (not zero-filled). Metrics reported with evaluated sample counts.

### Overall baselines (all eligible timestamps)
| Method | MAE | RMSE | n_evaluated | n_missing_predictions | n_unavailable_targets | coverage |
|---|---|---|---|---|---|---|
| `naive_previous_hour` | 1042.638 | 1544.074 | 2929 | 22 | 2 | 0.9919 |
| `seasonal_naive_lag24` | 1186.938 | 1940.403 | 2887 | 45 | 21 | 0.9776 |
| `seasonal_naive_lag168` | 1359.488 | 2195.350 | 2762 | 170 | 21 | 0.9353 |
| `historical_mean_past` | 3178.338 | 3732.998 | 2908 | 24 | 21 | 0.9848 |

### Test set segments (with per-method eligible masks)
| Method (test) | Overall MAE/RMSE (n) | Weekday MAE/RMSE (n) | Weekend MAE/RMSE (n) |
|---|---|---|---|
| `naive_previous_hour` | 599.390 / 959.852 (423) | 714.695 / 1090.668 (315) | 263.083 / 372.758 (108) |
| `seasonal_naive_lag24` | 871.114 / 1341.354 (414) | 1011.265 / 1497.776 (311) | 470.330 / 765.951 (103) |
| `seasonal_naive_lag168` | 1067.647 / 1646.178 (391) | 1257.783 / 1850.469 (291) | 490.740 / 858.138 (100) |
| `historical_mean_past` | 2041.300 / 2454.452 (419) | 2354.763 / 2739.239 (315) | 1120.327 / 1504.643 (104) |

**Common comparison (test, all of lag1/lag24/lag168 available):** 404 timestamps (identical eligible timestamps for fair comparison).

Full per-hour-of-day breakdowns are saved in `data/processed/baseline_results.json`.

## 6) Leakage-test results

Automated tests in `tests/forecasting/test_leakage.py` (run with `PYTHONPATH=src /usr/local/bin/python3 -m pytest tests/forecasting/test_leakage.py` or directly):

| Test | Status | Description |
|---|---|---|
| `test_no_feature_uses_future_demand` | **PASS** | Features use `.shift()` only; current hour not used as predictor |
| `test_lag24_exactly_24_hours_earlier` | **PASS** | lag-24 equals value exactly 24 UTC hours earlier |
| `test_lag168_exactly_168_hours_earlier` | **PASS** | lag-168 equals value exactly 168 UTC hours earlier |
| `test_missing_hour_gaps_not_compressed` | **PASS** | Gaps remain missing on continuous spine; no forward/backward fill; spine length preserved |
| `test_dst_transition_does_not_corrupt_lag_alignment` | **PASS** | Lag alignment based on UTC hourly position shift (continuous UTC spine) |
| `test_chronological_splits_do_not_overlap` | **PASS** | Splits contiguous, non-overlapping, chronological |
| `test_rolling_statistics_exclude_target_hour` | **PASS** | Rolling stats use `shift(1)` before rolling (target hour excluded) |

Synthetic fixtures used: deliberately missing hours (gap in sequence) and DST transition scenario. All tests passed.

## 7) Known limitations

1. **Ingestion latency unknown** - Assumed previous hour available at prediction time for naive baselines; configurable but not measured. Real deployment may need different assumptions.
2. **Boundary hours included** - `boundary` hours (96 total) are eligible but flagged as source-truncated (end-keyed archives omit late trips). Metrics include them per contract.
3. **Uncertain hours excluded** - DST fall-back ambiguous hours (2h) have NULL demand and are excluded from evaluation (correct per 4C).
4. **Outage hours excluded** - Unavailable hours absent from demand mart, excluded from evaluation.
5. **Historical mean baseline simple** - Expanding mean of past observations only; may be less competitive.
6. **No weather features** - Explicitly out of scope for Phase 5A.
7. **Generated datasets not committed** - `data/processed/*.parquet/csv` and metadata are generated artifacts (avoid committing large files per instructions).
8. **DB dependency for dataset build** - `scripts/build_forecast_dataset.py` reads from live Postgres marts; requires DB running with Phase 4C data loaded.

## 8) Readiness assessment for Phase 5B

**Ready to proceed to Phase 5B** with the following conditions met:

- [x] **Forecasting contract defined** and documented with explicit assumptions
- [x] **Coverage eligibility rules** established (complete/boundary/partial eligible; uncertain/unavailable excluded), consistent with Phase 4C
- [x] **Leakage-safe feature pipeline** implemented on continuous UTC spine with no forward-fill/compression of gaps; lags/rolls exclude target/current hour
- [x] **Baselines implemented** (previous-hour naive, lag-24, lag-168, historical mean) with unavailable marked (not zero-filled)
- [x] **Chronological splits** fixed and non-overlapping
- [x] **Comprehensive leakage tests** with synthetic fixtures pass
- [x] **Reproducible commands** provided to build dataset, run baselines, evaluate
- [x] **Metrics reported with sample counts** and segments (weekday/weekend, hour-of-day)

**Recommendations for Phase 5B:**
- Consider weather enrichment carefully (Open-Meteo DST note from README - join on UTC)
- Feature selection from leakage-safe set
- More sophisticated models with proper validation
- Consider boundary hour handling (weighting or separate evaluation)
- Measure/estimate actual ingestion latency if possible

## Reproducible commands

```bash
# Build feature dataset from marts (requires Postgres with Phase 4C data)
PYTHONPATH=src /usr/local/bin/python3 scripts/build_forecast_dataset.py

# Run baselines and evaluation
PYTHONPATH=src /usr/local/bin/python3 scripts/run_baselines.py

# Run leakage tests
PYTHONPATH=src /usr/local/bin/python3 -m pytest tests/forecasting/test_leakage.py -v
```

**Artifacts:**
- `src/forecasting/*` - contract, config, features, dataset builder, baselines, evaluation
- `scripts/build_forecast_dataset.py`, `scripts/run_baselines.py`
- `tests/forecasting/test_leakage.py`
- `data/processed/forecast_dataset.csv`, `data/processed/forecast_dataset.metadata.json`
- `data/processed/baseline_results.json`
- `config/forecasting_config.json` (if generated)
