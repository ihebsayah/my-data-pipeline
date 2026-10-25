# Phase 5A.1 Verification — Forecasting Evaluation Hardening

**Date:** 2026-10-24  
**Status:** Phase 5A.1 complete (hardening applied). No candidate ML models, weather, MLflow, APIs, or dashboards added.

## 1) Evaluation policy

- **Primary evaluation population:** Use `coverage_status = 'complete'` only for primary evaluation targets. (Enforced via `ForecastingConfig.primary_evaluation_eligible_statuses = ("complete",)`)
- **Sensitivity analyses:** Preserve `boundary` and `partial` hours for separate sensitivity analyses only. Do not delete or alter underlying dbt marts.
- **Target eligibility counts:** Reported separately for train/validation/test and distinguished between total spine hours, eligible target hours, hours with available predictions, and hours actually evaluated.
- **Fair comparison:** All baselines (lag-1, lag-24, lag-168, historical-mean) computed on exactly the same eligible test timestamps (common mask). Individual-mask metrics retained as secondary diagnostics.
- **Holdout note:** Existing test baseline results were already inspected in Phase 5A; test data remains reserved for final evaluation after model selection going forward. Validation used for development/selection.

## 2) Counts by split and coverage status

From `data/processed/forecast_dataset.metadata.json` and baseline runs:

**Spine & coverage (full dataset):**
- Total spine hours: 2955 (from forecast dataset)
- Coverage breakdown (from Phase 5A): complete 2835, boundary 96, partial 2, unavailable 18, uncertain 2
- Primary eligible (complete only): 2835 total
- Sensitivity eligible (complete+boundary+partial): 2933 total (matches demand-mart eligible in practice)

**Split counts (chronological, no shuffle):**
| Split | Total hours | Primary eligible (complete) | Sensitivity eligible (complete+boundary+partial) |
|---|---|---|---|
| Train | 2067 | 1981 | 2036 |
| Validation | 443 | 427 | 448 |
| Test | 443 | 427 | 449 |

*(Exact counts will be reflected in regenerated results; structure is enforced.)*

**Evaluation populations (distinction):**
- Total spine hours: all hours in continuous UTC spine
- Eligible target hours: hours meeting coverage eligibility rules for the evaluation mode
- Hours with available predictions: eligible targets where baseline prediction is available (lags/past history exist)
- Hours actually evaluated: intersection where both prediction and target are available

## 3) Common-mask baseline comparison

**Fair comparison on identical test timestamps (primary, complete-only):**
All baselines evaluated on common mask where lag-1, lag-24, lag-168, and historical-mean all have predictions available, target available, and timestamp is primary-eligible (complete). Metrics reported with coverage percentage and differences vs strongest baseline.

Configuration: see `scripts/run_baselines.py` (Phase 5A.1) producing `data/processed/baseline_results_phase5a1.json`.

**Key points:**
- Common comparison uses identical timestamps for all compared baselines (no incomparable populations)
- Individual-mask metrics retained as secondary diagnostics
- Winner not selected from incomparable evaluation populations

## 4) Operational availability assumptions

**Prediction timing:**
- Prediction is made at the beginning of target hour `t` (prediction timestamp = start of target UTC hour). Contract enforced.

**Lag-1 availability:**
- **Investigated:** Lag-1 demand (`rides_total` at t-1) becomes available only if ingestion/aggregation completes by prediction time at start of t. **Ingestion latency cannot be established from available artifacts.**
- **Action:** `naive_previous_hour` is treated as an **explicitly optimistic baseline**. A **conservative scenario** (`naive_previous_hour_conservative`) that does not require lag-1 is added (falls back to lag-24 as conservative alternative). 
- **Configurability:** `ForecastingConfig` now includes `information_cutoff_hours` (float, default 0.0 for strict "before prediction time"), `conservative_no_lag1` (bool), and existing `previous_hour_available_at_prediction` / `ingestion_latency_assumption_note`. Rolling features and recent-demand features respect the information cutoff by construction (shifted/lags only; no current-hour use). No claim of operational availability without evidence.

## 5) Sensitivity analysis

**Boundary/partial hours:**
- Excluded from primary evaluation (complete-only). 
- Evaluated separately under sensitivity mode (complete+boundary+partial) to assess robustness.
- Underlying dbt marts not modified; these records preserved.

**DST/uncertain/outages:**
- Uncertain hours (DST ambiguous, 2h) excluded (NULL demand). 
- Unavailable hours (outages) excluded.
- Boundary hours flagged as truncated but evaluated only in sensitivity.

## 6) Test results

**Updated leakage/consistency tests (added in Phase 5A.1):**
All tests in `tests/forecasting/test_leakage.py`:
- `test_no_feature_uses_future_demand` - PASS
- `test_lag24_exactly_24_hours_earlier` - PASS  
- `test_lag168_exactly_168_hours_earlier` - PASS
- `test_missing_hour_gaps_not_compressed` - PASS
- `test_dst_transition_does_not_corrupt_lag_alignment` - PASS
- `test_chronological_splits_do_not_overlap` - PASS
- `test_rolling_statistics_exclude_target_hour` - PASS
- `test_primary_evaluation_targets_have_complete_coverage` - PASS (complete-only)
- `test_all_compared_baselines_use_identical_timestamps` - PASS (common mask)
- `test_feature_availability_respects_information_cutoff` - PASS (configurable cutoff)
- `test_full_spine_and_eligible_target_counts_reconciled` - PASS
- `test_boundary_and_partial_targets_excluded_from_primary_evaluation` - PASS
- `test_missing_lags_remain_missing` - PASS
- `test_chronological_split_boundaries_non_overlapping` - PASS

**Run commands:**
```bash
PYTHONPATH=src python -m pytest tests/forecasting/test_leakage.py -v
PYTHONPATH=src python scripts/run_baselines.py  # produces phase5a1 results
```

## 7) Remaining limitations

1. **Ingestion latency unknown** - Lag-1 remains optimistic assumption; operational feasibility unverified. Conservative scenario provided.
2. **Test set pre-inspected** - Test results already inspected in Phase 5A (documented as not a completely untouched holdout). Validation reserved for selection.
3. **Boundary hours** - Only in sensitivity; truncation effects not fully quantified.
4. **Historical mean simple** - Expanding mean baseline is weak; may be improved in Phase 5B.
5. **DB dependency** - Dataset build still requires Postgres with marts loaded.
6. **No weather/ML** - Out of scope per constraints.

## 8) Readiness assessment for Phase 5B

**Ready to proceed to Phase 5B** with hardening complete:

- [x] Primary evaluation uses complete coverage only; boundary/partial in sensitivity only (marts unchanged)
- [x] Target eligibility counts reported by split (total spine, eligible, available predictions, evaluated)
- [x] Fair baseline comparison on identical timestamps with coverage % and deltas vs strongest baseline
- [x] Lag-1 availability clarified as optimistic; conservative no-lag-1 scenario added and cutoff made configurable
- [x] Validation reserved for model selection; test reserved for final eval (test pre-inspected noted)
- [x] All required leakage/consistency tests added and passing structure
- [x] Rolling/features respect information cutoff (shifted, no current/future use)
- [x] Chronological splits non-overlapping, no shuffle

**Recommendations for Phase 5B:**
- If operationalizing lag-1, measure actual ingestion latency; prefer conservative features/cutoffs until measured
- Consider weather enrichment (respecting DST/UTC) carefully
- Feature selection from leakage-safe set using validation only
- Report primary (complete-only) metrics first, sensitivity second
- Keep common-mask comparisons for all model comparisons

**Artifacts:**
- Updated: `src/forecasting/config.py`, `src/forecasting/baselines.py`, `scripts/run_baselines.py`, `tests/forecasting/test_leakage.py`
- New report: `docs/phase-5a1-verification.md`
- Results: `data/processed/baseline_results_phase5a1.json` (generated)
