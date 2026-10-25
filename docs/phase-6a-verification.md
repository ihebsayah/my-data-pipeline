# Phase 6A Verification: Historical Robustness and Model Reliability

**Date:** 2026-10-25

**Scope:** Assess whether the frozen Phase 5B HistGradientBoosting model is
robust across historical coverage conditions and segments, quantify the
effects of the January 2026 outage and boundary/partial history, verify
inference reliability, and estimate the cost of expanding history by 6-12
months.

**Frozen model:** Phase 5B `HistGradientBoostingRegressor` with
`learning_rate=0.03`, `max_iter=200`, `max_leaf_nodes=15`, and
`random_state=42`. The 27-feature contract and t-2 information cutoff are
unchanged.

**Important test caveat:** The test split was inspected in Phase 5A before the
Phase 5B model was frozen. It is not a pristine holdout. Phase 6A does not
retune, replace, or select a model using test results. All test results below
are descriptive robustness checks.

**Repository milestone:** The Phase 4C-5B work was committed as `1bc300f`
(`feat: complete historical expansion and forecasting phases 4C-5B`) and
tagged `v0.5b`. It was not pushed. Phase 6A changes remain uncommitted for
review.

---

## 1. Frozen Model and Artifact Preservation

### Frozen contract

- Target: `rides_total`.
- Prediction time: start of target UTC hour `t`.
- Latest permitted demand observation: `t-2`.
- Features: 14 calendar features and 13 historical-demand features
  (`lag_2`, `lag_24`, `lag_48`, `lag_72`, `lag_168`; four statistics over
  `roll2_*_24` and `roll2_*_168`).
- `lag_1` and the old Phase 5A `roll_*` shift(1) columns are excluded.
- Missing hours remain rows with `NaN`; no fill, interpolation, or row
  compression.

### Preservation result

The Phase 6A analysis loaded the frozen artifacts read-only. Manifest hashes
and byte counts were identical before and after analysis for all six files:

| Artifact | SHA-256 | Bytes |
|---|---|---:|
| `feature_list.json` | `fbdbf3096ff2fc2dd8a1657f99d9eda58c2dd784ccb1cb1d41f263d45de51cbe` | 4,150 |
| `final_model.joblib` | `9163075eb3ac7e8b474441574a434d3dbb67f41943cc065571b32ec6f5fc274f` | 392,780 |
| `final_test_metrics.json` | `49580ead5001cb207fb8d64f5eae837a1dc0b373c7224f82c33631f128d8dffa` | 12,698 |
| `frozen_model_config.json` | `297246d9745dc7c5b4398665f16f7c634cf3d4e254ff9690e80d5465745a7a47` | 4,629 |
| `hyperparameter_search.json` | `50bb89dda1de7f1c288c951ac78bbe67e57b10f86fecc7e7cb8adb5dd2a2aee7` | 8,026 |
| `validation_metrics.json` | `284a83f59e567bca05ab24507a36acf553e4a48f582d99f71252266ab078db0f` | 16,854 |

The dataset SHA-256 matched the frozen configuration:
`bbabaf23211482321edb75051ea111df121209e62763adc4c72d838c1c1aafee`.

The Phase 5B metrics were reproduced exactly on the same common masks:

| Split | Common mask n | Frozen HGB MAE | Frozen HGB RMSE | Phase 5B MAE | Absolute delta |
|---|---:|---:|---:|---:|---:|
| Validation | 419 | 550.686 | 804.011 | 550.686 | 0.000 |
| Final test | 380 | 534.645 | 775.304 | 534.645 | 0.000 |

`phase5b_reproduction.json` records `reproduced: true`. No Phase 5B artifact
was overwritten. The Phase 6A sensitivity model is stored separately under
`data/processed/phase6a/`.

---

## 2. Historical Expansion Feasibility

### S3 availability

The Citi Bike S3 listing was queried at `2026-10-25T15:17:07Z`. The listing
contained 178 objects. All months required by the candidate windows were
available:

- `2024-10` through `2025-09` inclusive.
- The existing dataset already covers `2025-10` through `2026-01`.

The archive sizes for `2025-01` through `2025-09` range from 396,008,433 bytes
(February) to 1,032,411,368 bytes (September).

### Measured Phase 4C baseline

| Metric | Measured value |
|---|---:|
| Existing rows | 12,056,963 |
| Existing archive bytes | 2,352,849,251 |
| Archive bytes per row | 195.144 |
| Raw `trips` size | 5,045 MiB |
| Staging `stg_trips` size | 4,743 MiB |
| Database size | 9,797 MiB |
| Database bytes per row | 851.248 |
| dbt full refresh | 39m31s |
| Host free space at analysis time | 112.95 GiB |

Measured monthly ingestion was 43.9 minutes for 202510, 35.5 minutes for
202511, 23.6 minutes for 202512, and 11.0 minutes for 202601.

### Candidate expansion windows

All estimates use measured archive bytes per row, measured database bytes per
row, and the measured dbt full-refresh time. Processing estimates use 1,500
rows/second with a 25% buffer. Required free space includes additional database
bytes, archive cache, and a 25% buffer.

| Candidate | New archives | Total months | Estimated new rows | Additional DB | Archive cache | Required free | Ingestion h | dbt h | Total h |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 6 new months: 2025-04..2025-09 | 6 | 10 | 28,221,582 | 22.37 GiB | 5.13 GiB | 34.38 GiB | 6.53 | 2.75 | 9.28 |
| **9 new months: 2025-01..2025-09 (recommended)** | **9** | **13** | **35,541,709** | **28.18 GiB** | **6.46 GiB** | **43.30 GiB** | **8.23** | **3.25** | **11.48** |
| 12 new months: 2024-10..2025-09 | 12 | 16 | 46,721,925 | 37.04 GiB | 8.49 GiB | 56.91 GiB | 10.82 | 4.01 | 14.83 |

The projected database size after the recommended expansion is 37.74 GiB.
Required free space is 43.30 GiB, leaving substantial headroom against 112.95
GiB free.

### Recommendation

Expand with **2025-01-01 through 2025-09-30**, adding nine consecutive monthly
archives. This produces 13 consecutive months with the existing four months and
covers winter, spring, and summer seasonality. It is a better robustness
investment than the six summer months alone and a lower-risk first step than a
full additional year.

Operational plan if approved later:

1. Add DST-correct seed rows for each new month.
2. Ingest one archive at a time and reconcile counts after each month.
3. Rebuild dbt and rerun coverage assertions.
4. Rebuild the forecasting dataset and verify the continuous UTC hourly spine.
5. Freeze a new model only after a new train/validation/test protocol is defined.

Do not zero-fill unverified demand. Missing and unavailable hours must remain
`NaN`. The estimates assume linear scaling; incremental staging should be
considered before rebuilding 47.6 million rows.

---

## 3. Frozen-Model Robustness

### Sample counts

| Split | Spine hours | Target not null | Complete-coverage targets | Common mask |
|---|---:|---:|---:|---:|
| Train | 2,067 | 2,065 | 2,017 | 1,991 |
| Validation | 443 | 443 | 419 | 419 |
| Test | 443 | 424 | 399 | 380 |

The test common mask is 380 because 19 complete hours after the outage lack a
valid lag-24 observation. The 20 target-null hours are already excluded from
the 399 complete-coverage targets.

### Overall out-of-sample performance

| Split | Model MAE | Model RMSE | Lag-24 MAE | Lag-24 RMSE | MAE improvement |
|---|---:|---:|---:|---:|---:|
| Validation (n=419) | 550.69 | 804.01 | 896.95 | 1,422.49 | +38.60% |
| Test (n=380) | 534.65 | 775.30 | 716.78 | 1,149.69 | +25.41% |

### Weekday versus weekend

| Split | Segment | n | Model MAE | Model RMSE | Lag-24 MAE | MAE improvement |
|---|---|---:|---:|---:|---:|---:|
| Validation | Weekday | 275 | 514.66 | 735.33 | 853.47 | +39.70% |
| Validation | Weekend | 144 | 619.49 | 921.06 | 980.00 | +36.79% |
| Test | Weekday | 296 | 519.35 | 740.43 | 550.92 | +5.73% |
| Test | Weekend | 84 | 588.54 | 887.33 | 1,301.24 | +54.77% |

The test weekday advantage is small. The test weekend advantage is large, but
only 84 observations support it.

### By month

| Split | Month | n | Model MAE | Model RMSE | Lag-24 MAE | MAE improvement |
|---|---|---:|---:|---:|---:|---:|
| Train (in-sample) | 10 | 696 | 438.13 | 625.01 | 1,551.52 | +71.76% |
| Train (in-sample) | 11 | 693 | 410.68 | 596.48 | 1,331.81 | +69.16% |
| Train (in-sample) | 12 | 602 | 397.53 | 555.17 | 1,099.79 | +63.85% |
| Validation | 12 | 118 | 387.19 | 506.70 | 583.42 | +33.63% |
| Validation | 1 | 301 | 614.78 | 893.98 | 1,019.87 | +39.72% |
| Test | 1 | 380 | 534.65 | 775.30 | 716.78 | +25.41% |

Train rows are in-sample diagnostics only and are not evidence of
generalization. Out-of-sample month coverage is limited to December and
January.

### By demand-volume quartile

Quartile edges were derived from complete-coverage training targets:

- Q1: `rides_total <= 1,326`
- Q2: `1,326 < rides_total <= 4,208`
- Q3: `4,208 < rides_total <= 7,337`
- Q4: `rides_total > 7,337`

| Split | Quartile | n | Model MAE | Model RMSE | Lag-24 MAE | MAE improvement |
|---|---|---:|---:|---:|---:|---:|
| Validation | Q1 | 173 | 280.50 | 392.13 | 460.29 | +39.06% |
| Validation | Q2 | 166 | 630.61 | 865.36 | 1,054.13 | +40.18% |
| Validation | Q3 | 60 | 807.68 | 1,037.67 | 1,404.65 | +42.50% |
| Validation | Q4 | 20 | 1,453.48 | 1,663.41 | 1,846.40 | +21.28% |
| Test | Q1 | 174 | 472.21 | 792.47 | 326.42 | **-44.66%** |
| Test | Q2 | 130 | 575.56 | 761.18 | 1,023.77 | +43.78% |
| Test | Q3 | 62 | 548.60 | 660.60 | 1,014.19 | +45.91% |
| Test | Q4 | 14 | 868.88 | 1,094.28 | 1,400.71 | +37.97% |

The frozen model is materially worse than lag-24 on the test Q1 low-demand
segment. Q4 has only 14 test observations, so its estimate is fragile.

### By hour of day

Local-hour validation and test metrics, with test improvement versus lag-24:

| Hour | Val n | Val HGB MAE | Test n | Test HGB MAE | Test lag-24 MAE | Test MAE improvement |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 17 | 361.12 | 15 | 173.96 | 195.40 | +10.97% |
| 1 | 17 | 292.61 | 15 | 151.10 | 139.33 | -8.45% |
| 2 | 18 | 190.17 | 15 | 161.31 | 103.73 | -55.51% |
| 3 | 18 | 118.10 | 15 | 155.24 | 58.27 | -166.43% |
| 4 | 18 | 78.91 | 15 | 125.94 | 56.27 | -123.82% |
| 5 | 18 | 203.37 | 15 | 215.54 | 171.47 | -25.70% |
| 6 | 18 | 525.86 | 15 | 525.31 | 502.20 | -4.60% |
| 7 | 18 | 579.16 | 16 | 952.26 | 1,166.56 | +18.37% |
| 8 | 18 | 816.94 | 17 | 873.15 | 1,774.82 | +50.80% |
| 9 | 18 | 691.62 | 17 | 677.42 | 1,095.65 | +38.17% |
| 10 | 18 | 684.29 | 17 | 701.92 | 622.76 | -12.71% |
| 11 | 18 | 680.29 | 17 | 882.34 | 601.18 | -46.77% |
| 12 | 18 | 518.37 | 15 | 758.35 | 582.53 | -30.18% |
| 13 | 17 | 681.97 | 16 | 736.93 | 757.44 | +2.71% |
| 14 | 17 | 739.32 | 16 | 550.97 | 804.00 | +31.47% |
| 15 | 17 | 816.59 | 16 | 574.85 | 834.75 | +31.13% |
| 16 | 17 | 897.93 | 16 | 657.12 | 1,017.38 | +35.41% |
| 17 | 17 | 1,106.15 | 16 | 904.56 | 1,701.25 | +46.83% |
| 18 | 17 | 913.94 | 16 | 737.48 | 1,531.25 | +51.84% |
| 19 | 17 | 652.78 | 16 | 553.04 | 1,085.88 | +49.07% |
| 20 | 17 | 424.53 | 16 | 636.17 | 749.00 | +15.06% |
| 21 | 17 | 446.00 | 16 | 389.88 | 593.12 | +34.27% |
| 22 | 17 | 467.14 | 16 | 305.84 | 423.19 | +27.73% |
| 23 | 17 | 386.39 | 16 | 242.86 | 313.31 | +22.48% |

The model is strongest in daytime and evening hours and weaker than lag-24 in
several early-morning hours (test hours 1-6) and late-morning hours (10-12).
The 24-hour sample sizes are small, so hour-level rankings are descriptive.

---

## 4. Outage and Boundary Sensitivity

### January 2026 outage

The dataset contains 18 unavailable hours from
`2026-01-25 18:00 UTC` through `2026-01-26 11:00 UTC` inclusive
(`end_utc_exclusive = 2026-01-26 12:00 UTC`). The test split has 399
complete-coverage targets and 380 common-mask observations.

After the outage, 112 complete test targets remain, of which 93 enter the
common mask. The other 19 lack a valid lag-24 value. The 168-hour rolling
history is incomplete for all 93 common post-outage observations; the 24-hour
history is incomplete for 5.

| Test period | n | Model MAE | Model RMSE | Lag-24 MAE | Lag-24 RMSE | MAE improvement |
|---|---:|---:|---:|---:|---:|---:|
| Pre-outage | 287 | 484.72 | 695.64 | 884.38 | 1,311.16 | +45.19% |
| Post-outage | 93 | 688.72 | 981.19 | 199.56 | 309.06 | **-245.12%** |

This is the most important operational finding in Phase 6A. The frozen model
beats lag-24 strongly before the outage but is substantially worse than lag-24
after it. The likely mechanism is the 168-hour rolling features remaining
missing for a full week after the 18-hour gap. The model handles `NaN`
technically but loses its medium-term history. This is a reliability
limitation, not a reason to retune on the test set.

### Boundary and partial source history

The dataset contains:

- 96 `boundary` source hours.
- 2 `partial` source hours.
- 0 boundary/partial source hours with a zero target.

No zero fill is used. The quality check asks which prediction timestamps consume
a boundary or partial observation through a lag or rolling feature:

| Split | Complete targets | Affected | Unaffected |
|---|---:|---:|---:|
| Train | 2,017 | 263 | 1,754 |
| Validation | 419 | 169 | 250 |
| Test | 399 | 5 | 394 |
| All complete | 2,835 | 437 | 2,398 |

Affected-feature counts across all rows:

| Feature group | Affected rows |
|---|---:|
| `lag_2` | 95 |
| `lag_24` | 73 |
| `lag_48` | 73 |
| `lag_72` | 73 |
| `lag_168` | 72 |
| Each `roll2_*_24` | 164 |
| Each `roll2_*_168` | 431 |

The validation split is materially exposed: 169 of 419 complete targets
(40.3%) have at least one boundary/partial observation in their historical
features. The test split has only 5 affected complete targets, all around
`2026-01-26` through `2026-01-29`.

### Masked-history sensitivity

Two sensitivity experiments were run without changing the frozen model:

1. **Frozen model, masked history:** recompute features with boundary/partial
   target values set to `NaN`. The model is unchanged.
2. **Retrained sensitivity model, masked history:** fit the same HGB
   hyperparameters on the masked-history training frame. This is not a
   replacement candidate and was not selected using test performance.

| Split | Method | n | MAE | RMSE |
|---|---|---:|---:|---:|
| Validation | Frozen current | 419 | 550.69 | 804.01 |
| Validation | Frozen, masked boundary/partial history | 419 | 579.42 | 866.57 |
| Validation | Retrained, masked history | 419 | 559.86 | 841.80 |
| Validation | Lag-24 | 419 | 896.95 | 1,422.49 |
| Test | Frozen current | 380 | 534.65 | 775.30 |
| Test | Frozen, masked boundary/partial history | 380 | 542.36 | 797.08 |
| Test | Retrained, masked history | 380 | 495.60 | 793.98 |
| Test | Lag-24 | 380 | 716.78 | 1,149.69 |

Masking boundary/partial history worsens validation MAE by 28.73 (5.2%) and
test MAE by 7.72 (1.4%). The retrained masked-history model improves test MAE
by 39.04 versus the frozen model, but it is worse on validation (559.86 versus
550.69). Because the test split is not pristine and the frozen model is not
being replaced, this is reported as sensitivity evidence only.

Affected versus unaffected subsets:

| Split | Subset | n | Frozen current MAE | Frozen masked MAE | Retrained masked MAE | Lag-24 MAE |
|---|---|---:|---:|---:|---:|---:|
| Validation | Unaffected | 250 | 526.01 | 526.01 | 536.42 | 966.98 |
| Validation | Affected | 169 | 587.18 | 658.42 | 594.53 | 793.37 |
| Test | Unaffected | 375 | 522.75 | 522.75 | 475.99 | 719.42 |
| Test | Affected | 5 | 1,426.67 | 2,013.15 | 1,966.32 | 518.60 |

Unaffected rows are unchanged by construction. The affected validation subset
degrades by 71.24 MAE (12.1%) when boundary/partial history is masked. The test
affected subset has only 5 observations and is not a reliable basis for a
model decision.

---

## 5. Reliability Checks

### Prediction health

| Split | n | Non-finite | Negative | Min | Max |
|---|---:|---:|---:|---:|---:|
| Train | 2,017 | 0 | 0 | 86.83 | 18,787.65 |
| Validation | 419 | 0 | 0 | 36.85 | 10,183.72 |
| Test | 399 | 0 | 0 | 75.56 | 10,741.69 |
| All complete | 2,835 | 0 | 0 | 36.85 | 18,787.65 |

All frozen predictions are finite and nonnegative. The model is evaluated
without post-hoc clipping. `clip_nonnegative` is available for a future serving
layer and is a no-op on the observed predictions.

### Interface checks

| Check | Result |
|---|---|
| Frozen feature list matches config | Pass |
| Missing feature column | `FeatureSchemaError` with missing column name |
| Infinite feature value | `NonFiniteFeatureError` |
| Non-numeric feature value | `ValueError` |
| All-`NaN` feature row | Finite prediction (4,390.94 in the smoke test) |
| Extra feature columns | Ignored; predictions unchanged |
| Deterministic inference | 3 repeated runs identical |
| Negative, zero, or huge `lag_2` values | Finite predictions; no clipping |
| Nonnegative helper | Clips correctly and is idempotent |

The frozen HGB tolerates `NaN` as designed. It does not perform semantic range
validation for impossible calendar values or implausible demand values. A
future serving layer must validate ranges, reject `inf`, and decide whether to
clip negative predictions.

No serving API, scheduler, or monitoring stack was added in Phase 6A.

---

## 6. Evidence Supporting Current Model

The frozen Phase 5B model remains supported as the current reference model under
normal complete-history conditions:

- It reproduces Phase 5B validation and test metrics exactly.
- It beats lag-24 on the same validation mask by 38.60% MAE and 43.48% RMSE.
- It beats lag-24 on the same test mask by 25.41% MAE and 32.56% RMSE.
- It is stronger on weekends (test: +54.77% MAE), Q2/Q3/Q4 volume segments, and
  afternoon/evening hours.
- It loads deterministically, accepts the frozen schema, handles `NaN`
  history, returns finite nonnegative predictions, and rejects missing,
  infinite, or non-numeric features.
- All Phase 5B artifacts remain byte-for-byte unchanged.

The support is conditional:

- The test weekday advantage is only 5.73%.
- The test Q1 low-demand segment is 44.66% worse than lag-24.
- The test post-outage segment is 245.12% worse than lag-24.
- Test affected-history and hour-level slices are small.
- The test split was already inspected during Phase 5A.

The appropriate conclusion is that the frozen model is a valid current
benchmark and the best model evaluated so far on validation and overall test,
but it is not yet a production-ready forecaster without outage handling,
low-demand monitoring, and a truly untouched evaluation period.

---

## 7. Limitations and Next Steps

### Limitations

1. **No pristine holdout.** Test was inspected in Phase 5A. Phase 6A cannot
   provide an unbiased final generalization estimate.
2. **Limited seasonal coverage.** Four months of data cannot establish
   year-round robustness. Historical expansion is the highest-value next
   evidence step.
3. **Post-outage degradation.** The 168-hour rolling features recover slowly;
   the model underperforms lag-24 for the entire post-outage test window.
4. **Small sensitive slices.** Test Q1 has 174 rows but is worse than lag-24;
   test affected-boundary history has 5 rows; Q4 has 14 rows.
5. **Boundary/partial dependence.** Validation has 40.3% affected complete
   targets. Masking those source observations changes validation MAE by 5.2%.
6. **No exogenous signals.** Weather, events, holidays beyond calendar flags,
   and station-level behavior are not modeled.
7. **Descriptive segments.** Segment comparisons are not causal and no
   multiplicity-adjusted significance tests were performed.
8. **Expansion estimates are linear.** Actual ingestion and dbt times will vary
   with table size, indexes, and storage.

### Recommended next steps

1. Approve and execute the `2025-01-01` through `2025-09-30` expansion using
   month-by-month ingestion, reconciliation, and dbt coverage checks.
2. Rebuild the forecasting dataset and verify the continuous UTC hourly spine,
   DST handling, and boundary/partial counts.
3. Define a new time-based evaluation protocol with a genuinely untouched
   final test period before freezing another model.
4. Add outage-aware behavior: a lag-24 fallback, shorter-window rolling
   features, or explicit history-recovery indicators. Evaluate these on
   validation, not on the already-inspected test.
5. Add a serving-layer schema and range validator; reject `inf`, validate
   calendar ranges, and decide on nonnegative clipping.
6. Monitor low-demand and post-outage segments separately before using the
   model operationally.
7. Do not retune the current frozen model on the Phase 5B test split. Preserve
   it as the comparison baseline.

Phase 6A ends here. No historical expansion was executed, no Phase 5B artifact
was modified, and no Phase 6A changes were committed.

---

## Reproducibility

Commands:

```bash
PYTHONPATH=src python3 scripts/phase6a_expansion_feasibility.py
PYTHONPATH=src python3 scripts/phase6a_analysis.py
PYTHONPATH=src python3 -m pytest tests/forecasting/test_phase6a.py -q
PYTHONPATH=src python3 -m pytest tests/unit tests/forecasting -q
CITI_TEST_POSTGRES=1 PYTHONPATH=src python3 -m pytest tests/integration -q
```

Verification results:

- Phase 6A tests: 9 passed.
- Unit plus forecasting: 49 passed.
- Integration with Postgres: 13 passed.
- Phase 5B reproduction: exact, zero delta.
- Frozen artifact manifest: unchanged.

Outputs:

- `data/processed/phase6a/expansion_feasibility.json`
- `data/processed/phase6a/segmented_analysis.json`
- `data/processed/phase6a/historical_feature_quality.json`
- `data/processed/phase6a/phase5b_reproduction.json`
- `data/processed/phase6a/reliability_checks.json`
- `data/processed/phase6a/frozen_artifact_preservation.json`
- `data/processed/phase6a/analysis_metadata.json`
- `data/processed/phase6a/phase6a_summary.json`
- `data/processed/phase6a/sensitivity_masked_history_model.joblib`

Code:

- `scripts/phase6a_expansion_feasibility.py`
- `scripts/phase6a_analysis.py`
- `src/forecasting/robustness.py`
- `src/forecasting/reliability.py`
- `tests/forecasting/test_phase6a.py`
