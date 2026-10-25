# Phase 5B Verification — Conservative Cutoff, Ridge/HGB Candidates, Validation, and Final Test

**Date:** 2026-10-25  
**Status:** Phase 5B complete. No commit was made. No Phase 6, weather, MLflow, FastAPI, Streamlit, Airflow, Docker, database, or unrelated infrastructure work was added.

## 1) Preflight pytest output and exit code

### Environment note

The interpreter on `PATH` is Python 3.13.3 at `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`. There is no `python` executable in this shell, so a temporary shim outside the repository was used only to make the required command resolve:

```bash
mkdir -p /var/folders/66/h67n6htj7w5cmdfx676wj6mm0000gn/T/opencode/phase5b-bin
ln -sf /usr/local/bin/python3 /var/folders/66/h67n6htj7w5cmdfx676wj6mm0000gn/T/opencode/phase5b-bin/python
```

All commands below were run from the repository root with `PYTHONPATH=src`.

### Baseline preflight (before Phase 5B tests)

Command:

```bash
PYTHONPATH=src python -m pytest tests/forecasting/ -v
```

First result: **exit code 2**, collected **0** items, **1 collection error**, **0 passed**, **0 failed**, **0 skipped**. Root cause: the empty `tests/forecasting/__init__.py` made pytest import `tests/forecasting/conftest.py` as `forecasting.conftest`, which shadowed `src/forecasting` in `sys.modules` and caused `ModuleNotFoundError: No module named 'forecasting.features'`.

Fix: removed the empty `tests/forecasting/__init__.py` marker (no tests were removed or changed). Re-ran the exact command.

Baseline rerun after the collection fix:

```text
collected 14 items
============================== 14 passed in 1.04s ==============================
EXIT_CODE=0
```

Counts: **collected 14, passed 14, failed 0, skipped 0, exit code 0**.

### Post-new-test preflight

Added `tests/forecasting/test_phase5b_cutoff.py::test_synthetic_t_minus_1_exists_but_is_unavailable_under_cutoff`, then reran:

```bash
PYTHONPATH=src python -m pytest tests/forecasting/ -v
```

Result:

```text
collected 15 items
============================== 15 passed in 0.87s ==============================
EXIT_CODE=0
```

Counts: **collected 15, passed 15, failed 0, skipped 0, exit code 0**.

The cutoff test uses a continuous synthetic UTC spine where `demand(t-1)` exists. It verifies that `lag_1` is removed from the primary feature frame, `lag_2 == demand(t-2)`, rolling 24/168 features are computed from `shift(2)` (newest observation `t-2`), and mutating `demand(t-1)` changes no conservative feature at `t`.

### Final full forecasting suite

```bash
PYTHONPATH=src python -m pytest tests/forecasting/ -v
```

Result:

```text
collected 25 items
============================== 25 passed in 3.75s ==============================
EXIT_CODE=0
```

Counts: **collected 25, passed 25, failed 0, skipped 0, exit code 0**.

Existing unit tests were also run unchanged:

```bash
PYTHONPATH=src python -m pytest tests/unit -q
```

Result: **15 passed, exit code 0**.

## 2) Final modeling contract

| Item | Phase 5B contract |
|---|---|
| Target | Citywide hourly Citi Bike starts, `rides_total`, for target UTC hour `t`. |
| Prediction time | Beginning of target UTC hour `t`. |
| Information cutoff | Conservative default: latest permitted observed demand is `t-2`. |
| `lag_1` | Prohibited. It is not computed by the primary builder, is removed if present in the input frame, and is not in the model feature list. |
| Rolling demand features | Computed from `rides_total.shift(2).rolling(...)`; the newest observation in every window is `t-2`. No `shift(1)` rolling features are selected. |
| Time alignment | Continuous UTC hourly spine; position-based lags are exact UTC-hour offsets. Non-continuous input frames are rejected. |
| Missing hours | Remain rows with NaN values. No forward-fill, backward-fill, interpolation, or row compression. |
| Training/evaluation targets | Only `coverage_status == "complete"` target hours. |
| Splits | Existing chronological 70/15/15 `get_chronological_splits`; no shuffle and no re-shuffling. |
| Model candidates | Ridge regression and `HistGradientBoostingRegressor` only. No neural networks, XGBoost, LightGBM, or deep learning. |
| Preprocessing | scikit-learn pipelines. Ridge: median imputer plus `StandardScaler`. HGB: native NaN handling, no explicit imputer. All preprocessing fit on training data only. |
| Hyperparameter search | `TimeSeriesSplit(n_splits=4)` within the training split only; `scoring="neg_mean_absolute_error"`; `n_jobs=1`; `random_state=42`. |
| Selection | Validation only. The final candidate is the lower validation common-mask MAE. Test is not used for selection. |
| Final test | Exactly one real evaluation on the existing test split after the configuration was frozen. |
| Prohibited predictors | Target components, coverage/audit fields, identifiers, and target. Specifically: `rides_member`, `rides_casual`, `coverage_status`, `coverage_reason`, `in_demand_mart`, `is_source_truncated_x`, `is_source_truncated_y`, `is_dst_ambiguous_hour`, `in_outage_window`, `hour_utc`, `hour_local_start`, `hour_local_date`, `rides_total`, `lag_1`, and all unsafe Phase 5A `roll_*` columns. |

Implementation: `src/forecasting/features.py`, `src/forecasting/modeling.py`, and `scripts/train_forecasting_models.py`. Existing `build_features` defaults were left unchanged for backward compatibility; the primary model uses the new `build_primary_features` path.

## 3) Feature list and cutoff policy

### Included primary features (27)

Calendar features known at prediction time (14):

```text
hour_local, day_of_week_local, is_weekend_local, month,
day_of_month_local, is_month_start, is_month_end, isodow_local,
hour_sin, hour_cos, dow_sin, dow_cos, month_sin, month_cos
```

Historical demand features using data at or before `t-2` (13):

```text
lag_2, lag_24, lag_48, lag_72, lag_168,
roll2_mean_24, roll2_std_24, roll2_min_24, roll2_max_24,
roll2_mean_168, roll2_std_168, roll2_min_168, roll2_max_168
```

`roll2_*` names make the cutoff explicit. Each is computed from `rides_total.shift(2).rolling(window, min_periods=window)`. Full windows are required, so a missing observation inside a window yields a missing feature rather than an imputed history value. The model-specific imputer/native-NaN handling deals with those missing features.

### Excluded features and reasons

| Excluded column(s) | Reason |
|---|---|
| `rides_total` | Target; never a predictor. |
| `rides_member`, `rides_casual` | Target components; ex-post information. |
| `coverage_status`, `coverage_reason`, `in_demand_mart`, `is_source_truncated_x`, `is_source_truncated_y`, `is_dst_ambiguous_hour`, `in_outage_window` | Coverage/audit flags; not predictors. |
| `hour_utc` | Time identifier; calendar features are derived from it. |
| `hour_local_start`, `hour_local_date` | Local-time identifiers; not predictors. |
| `lag_1` | Violates the conservative `t-2` cutoff. Removed from the primary frame. |
| `roll_mean_24`, `roll_std_24`, `roll_min_24`, `roll_max_24`, `roll_mean_168`, `roll_std_168`, `roll_min_168`, `roll_max_168` | Phase 5A columns use `shift(1)` and are unsafe for the `t-2` cutoff. They are not selected. |

### History policy

Historical features are computed from observed `rides_total` values on the continuous UTC spine. Boundary and partial observed values may enter history, but no coverage flag is a predictor, and missing hours remain NaN. This is a deliberate choice: the historical signal is the observed demand, while the target/evaluation population is restricted to complete-coverage hours. Non-complete target rows are never used to fit or evaluate the primary model.

Artifact: `data/processed/phase5b/feature_list.json`.

## 4) Training and validation counts

Dataset: `data/processed/forecast_dataset.csv`, 2,953 continuous UTC hourly rows, 38 columns, `2025-10-01 04:00 UTC` through `2026-02-01 04:00 UTC`, zero duplicate hours. SHA-256: `bbabaf23211482321edb75051ea111df121209e62763adc4c72d838c1c1aafee`.

Coverage distribution: complete 2,835; boundary 96; unavailable 18; uncertain 2; partial 2.

| Split | Spine hours | Complete-coverage targets | Complete start (UTC) | Complete end (UTC) |
|---|---:|---:|---|---|
| Train | 2,067 | 2,017 | 2025-10-01 04:00 | 2025-12-26 06:00 |
| Validation | 443 | 419 | 2025-12-26 07:00 | 2026-01-13 17:00 |
| Test | 443 | 399 | 2026-01-13 18:00 | 2026-01-31 04:00 |

The model was fit on **2,017 complete-coverage training targets**. Validation used **419 complete-coverage targets**. The final test common mask used **380 of 399 complete-coverage test targets** because the conservative `lag_24` baseline is unavailable for 19 test hours after the January 2026 source outage.

Chronological CV used only the 2,017 training rows; no validation or test row entered any search fold.

## 5) Candidate hyperparameters

Command:

```bash
PYTHONPATH=src python3 scripts/train_forecasting_models.py search
```

Exit code: **0**. Runtime: approximately 4 minutes 2 seconds. Artifact: `data/processed/phase5b/hyperparameter_search.json`.

| Candidate | Grid | Configurations | Best parameters | Best CV MAE |
|---|---|---:|---|---:|
| Ridge | `alpha ∈ {0.1, 0.3, 1, 3, 10, 30, 100}` | 7 | `alpha=100.0` | 1,082.705 |
| HistGradientBoosting | `learning_rate ∈ {0.03, 0.05, 0.1}`, `max_iter ∈ {200, 400}`, `max_leaf_nodes ∈ {15, 31}` | 12 | `learning_rate=0.03`, `max_iter=200`, `max_leaf_nodes=15` | 800.537 |

CV setup: `TimeSeriesSplit(n_splits=4)`, `scoring="neg_mean_absolute_error"`, `n_jobs=1`, `random_state=42`, train partition only.

Preprocessing choice:

- **Ridge:** `SimpleImputer(strategy="median", keep_empty_features=True)` then `StandardScaler`, then `Ridge(random_state=42)`. The imputer and scaler are inside the pipeline, so they are fit within each CV training fold and again on the full training split for validation/freezing; validation data never influences them.
- **HGB:** `HistGradientBoostingRegressor(loss="squared_error", early_stopping=False, random_state=42)` with native NaN handling. No explicit imputer was used.

## 6) Validation metrics and common-mask counts

Command:

```bash
PYTHONPATH=src python3 scripts/train_forecasting_models.py validate
```

Exit code: **0**. Runtime: approximately 5.4 seconds. Artifact: `data/processed/phase5b/validation_metrics.json`.

Common-mask formation: validation split **and** `coverage_status == "complete"` **and** `rides_total` notna **and** `lag_24` notna **and** Ridge prediction notna **and** HGB prediction notna. The optimistic lag-1 reference is excluded from this mask.

Common-mask count: **419**. Denominator: **419 complete-coverage validation targets**. Coverage: **419/419 = 100.00%**. All three compared series had predictions available on all 419 validation targets.

Sign convention for improvement: **positive = model better (lower error than lag-24)**. Absolute improvement is `lag24_metric - model_metric`; percentage improvement is `100 × (lag24_metric - model_metric) / lag24_metric`.

| Model | MAE | RMSE | n on common mask | Prediction coverage | MAE improvement vs lag-24 | RMSE improvement vs lag-24 |
|---|---:|---:|---:|---:|---:|---:|
| Ridge | 1,782.826 | 2,211.683 | 419 | 419/419 = 100.00% | -885.874 (-98.76%) | -789.195 (-55.48%) |
| HistGradientBoosting | **550.686** | **804.011** | 419 | 419/419 = 100.00% | **+346.266 (+38.60%)** | **+618.477 (+43.48%)** |
| `lag_24` baseline | 896.952 | 1,422.488 | 419 | 419/419 = 100.00% | 0 (reference) | 0 (reference) |

Weekday/weekend validation metrics on the same common mask:

| Model | Weekday MAE / RMSE (n) | Weekend MAE / RMSE (n) |
|---|---|---|
| Ridge | 1,864.246 / 2,315.773 (275) | 1,627.337 / 1,997.888 (144) |
| HistGradientBoosting | 514.659 / 735.327 (275) | 619.487 / 921.057 (144) |
| `lag_24` baseline | 853.465 / 1,274.125 (275) | 980.000 / 1,669.583 (144) |

Optimistic lag-1 reference — **NON-OPERATIONAL**, evaluated on its own available validation timestamps and **not** mixed into the fair common mask: MAE **650.547**, RMSE **1,028.134**, n=**419**, coverage 419/419. This reference assumes `demand(t-1)` is available at the start of hour `t`; ingestion latency has not been measured, so it must not be used for the primary operational claim.

Full local-hour validation metrics are in Section 9 and in `validation_metrics.json`.

## 7) Final selected model and selection rationale

Selected candidate: **HistGradientBoostingRegressor**.

- Frozen hyperparameters: `learning_rate=0.03`, `max_iter=200`, `max_leaf_nodes=15`, `loss="squared_error"`, `early_stopping=False`, `random_state=42`.
- Frozen feature set: the 27 features listed in Section 3.
- Frozen preprocessing: HGB native NaN handling; no explicit imputer or scaler.
- Frozen target policy: complete-coverage targets only; fit on 2,017 training rows.
- Frozen evaluation mask: complete-coverage targets with a non-missing `lag_24` baseline on the same timestamps.
- Frozen information cutoff: newest observed demand is `t-2`; `lag_1` is prohibited.

Selection rationale: HGB achieved the lowest validation common-mask MAE, **550.686**, versus Ridge **1,782.826**. It also beat the conservative `lag_24` baseline by **346.266 MAE (38.60%)** on validation. Ridge was substantially worse than the baseline in this conservative feature setup, so it was not selected. Selection used validation only; no test metric was used.

Command:

```bash
PYTHONPATH=src python3 scripts/train_forecasting_models.py freeze
```

Exit code: **0**. Runtime: approximately 5.1 seconds.

Frozen artifacts:

```text
data/processed/phase5b/frozen_model_config.json
data/processed/phase5b/final_model.joblib
data/processed/phase5b/feature_list.json
```

The final-test stage requires `status="frozen"`, a matching dataset SHA-256, matching feature columns, matching model type/hyperparameters, and refuses to overwrite an existing `final_test_metrics.json` by default.

## 8) Final test metrics versus lag-24

The real test split was evaluated **once**, after freezing. Command:

```bash
PYTHONPATH=src python3 scripts/train_forecasting_models.py final-test
```

Exit code: **0**. Artifact: `data/processed/phase5b/final_test_metrics.json`.

Common-mask formation: test split **and** `coverage_status == "complete"` **and** `rides_total` notna **and** `lag_24` notna **and** selected-model prediction notna. Common-mask count: **380**. Denominator: **399 complete-coverage test targets**. Coverage: **380/399 = 95.24%**. The selected model had predictions on all 399 complete test targets; `lag_24` was unavailable on 19, so the fair comparison uses 380.

| Model | MAE | RMSE | n on common mask | Prediction coverage | MAE improvement vs lag-24 | RMSE improvement vs lag-24 |
|---|---:|---:|---:|---:|---:|---:|
| Frozen HistGradientBoosting | **534.645** | **775.304** | 380 | 380/399 = 95.24% | **+182.137 (+25.41%)** | **+374.386 (+32.56%)** |
| `lag_24` baseline | 716.782 | 1,149.690 | 380 | 380/399 = 95.24% | 0 (reference) | 0 (reference) |

Weekday/weekend final-test metrics on the same common mask:

| Model | Weekday MAE / RMSE (n) | Weekend MAE / RMSE (n) |
|---|---|---|
| Frozen HistGradientBoosting | 519.349 / 740.430 (296) | 588.544 / 887.334 (84) |
| `lag_24` baseline | 550.922 / 859.010 (296) | 1,301.238 / 1,838.287 (84) |

Optimistic lag-1 reference — **NON-OPERATIONAL**, evaluated on its own available test timestamps (not the fair common mask): MAE **627.855**, RMSE **987.565**, n=**399**, coverage 399/399. It is separate from the 380-timestamp model/baseline comparison and was not used for selection.

**Test-set pre-inspection caveat:** the test split was already inspected during Phase 5A, 5A.1, 5A.2, and 5A.3 baseline work. It is therefore **not a pristine untouched holdout**. Phase 5B froze the model before the final test and did not tune or select using test performance. A separate synthetic dry-run of the final-test code path was executed before the real run; it used synthetic data and did not evaluate the real test split. No bug was found after the real test results were observed, and the real test was not rerun.

## 9) Segmented performance

Local hour is `hour_local` in `America/New_York`. Hourly cells are `MAE / RMSE (n)`. Some hours have only 15–18 observations, so hour-level comparisons are noisy.

### Validation, same 419-timestamp common mask

| Local hour | Ridge MAE / RMSE (n) | HGB MAE / RMSE (n) | lag-24 MAE / RMSE (n) |
|---|---|---|---|
| 0 | 1775.3 / 2069.7 (17) | 361.1 / 480.5 (17) | 414.4 / 558.5 (17) |
| 1 | 1833.0 / 2103.0 (17) | 292.6 / 473.1 (17) | 415.4 / 632.1 (17) |
| 2 | 1791.9 / 2074.4 (18) | 190.2 / 338.3 (18) | 310.3 / 507.5 (18) |
| 3 | 1852.6 / 2153.3 (18) | 118.1 / 178.8 (18) | 180.9 / 301.3 (18) |
| 4 | 1897.2 / 2228.8 (18) | 78.9 / 87.3 (18) | 78.6 / 121.2 (18) |
| 5 | 1817.2 / 2173.5 (18) | 203.4 / 301.6 (18) | 217.9 / 311.6 (18) |
| 6 | 1540.4 / 1969.0 (18) | 525.9 / 636.7 (18) | 614.8 / 871.6 (18) |
| 7 | 1325.9 / 1939.1 (18) | 579.2 / 784.1 (18) | 1231.4 / 1866.3 (18) |
| 8 | 1748.3 / 2196.7 (18) | 816.9 / 1109.8 (18) | 1808.0 / 2588.9 (18) |
| 9 | 1692.2 / 2050.1 (18) | 691.6 / 766.6 (18) | 971.5 / 1103.1 (18) |
| 10 | 2505.0 / 3011.4 (18) | 684.3 / 787.8 (18) | 780.4 / 923.7 (18) |
| 11 | 2317.8 / 2757.6 (18) | 680.3 / 840.9 (18) | 965.4 / 1208.6 (18) |
| 12 | 1995.7 / 2461.2 (18) | 518.4 / 680.4 (18) | 1091.5 / 1399.9 (18) |
| 13 | 2103.9 / 2494.7 (17) | 682.0 / 916.2 (17) | 1226.8 / 1543.8 (17) |
| 14 | 2023.8 / 2494.7 (17) | 739.3 / 1143.6 (17) | 1338.7 / 1740.6 (17) |
| 15 | 1835.9 / 2395.8 (17) | 816.6 / 1284.4 (17) | 1415.5 / 2037.4 (17) |
| 16 | 1567.9 / 2190.4 (17) | 897.9 / 1222.7 (17) | 1498.9 / 2220.8 (17) |
| 17 | 1606.9 / 2163.1 (17) | 1106.2 / 1450.4 (17) | 2045.5 / 2664.2 (17) |
| 18 | 1426.9 / 1919.3 (17) | 913.9 / 1161.9 (17) | 1664.4 / 2207.5 (17) |
| 19 | 1640.5 / 1991.9 (17) | 652.8 / 712.5 (17) | 1110.5 / 1533.0 (17) |
| 20 | 1820.2 / 2141.6 (17) | 424.5 / 546.8 (17) | 797.5 / 1076.0 (17) |
| 21 | 1637.7 / 1970.6 (17) | 446.0 / 501.0 (17) | 580.9 / 774.4 (17) |
| 22 | 1489.3 / 1817.4 (17) | 467.1 / 545.7 (17) | 460.8 / 625.4 (17) |
| 23 | 1490.8 / 1824.1 (17) | 386.4 / 463.7 (17) | 401.8 / 554.4 (17) |

### Final test, same 380-timestamp common mask

| Local hour | Frozen HGB MAE / RMSE (n) | lag-24 MAE / RMSE (n) |
|---|---|---|
| 0 | 174.0 / 230.2 (15) | 195.4 / 284.2 (15) |
| 1 | 151.1 / 179.9 (15) | 139.3 / 221.1 (15) |
| 2 | 161.3 / 179.8 (15) | 103.7 / 176.1 (15) |
| 3 | 155.2 / 186.8 (15) | 58.3 / 108.5 (15) |
| 4 | 125.9 / 144.0 (15) | 56.3 / 73.6 (15) |
| 5 | 215.5 / 275.6 (15) | 171.5 / 273.7 (15) |
| 6 | 525.3 / 643.2 (15) | 502.2 / 763.5 (15) |
| 7 | 952.3 / 1073.8 (16) | 1166.6 / 1603.2 (16) |
| 8 | 873.1 / 1567.9 (17) | 1774.8 / 2393.5 (17) |
| 9 | 677.4 / 802.6 (17) | 1095.6 / 1386.9 (17) |
| 10 | 701.9 / 866.8 (17) | 622.8 / 936.3 (17) |
| 11 | 882.3 / 1022.7 (17) | 601.2 / 907.9 (17) |
| 12 | 758.4 / 1117.1 (15) | 582.5 / 777.2 (15) |
| 13 | 736.9 / 1090.4 (16) | 757.4 / 1079.5 (16) |
| 14 | 551.0 / 709.8 (16) | 804.0 / 1124.9 (16) |
| 15 | 574.9 / 670.5 (16) | 834.8 / 1164.6 (16) |
| 16 | 657.1 / 846.7 (16) | 1017.4 / 1467.5 (16) |
| 17 | 904.6 / 1097.8 (16) | 1701.2 / 2125.2 (16) |
| 18 | 737.5 / 838.9 (16) | 1531.2 / 1858.3 (16) |
| 19 | 553.0 / 651.0 (16) | 1085.9 / 1340.8 (16) |
| 20 | 636.2 / 724.1 (16) | 749.0 / 914.8 (16) |
| 21 | 389.9 / 453.8 (16) | 593.1 / 741.1 (16) |
| 22 | 305.8 / 338.4 (16) | 423.2 / 534.4 (16) |
| 23 | 242.9 / 298.1 (16) | 313.3 / 393.9 (16) |

The selected model beats `lag_24` on aggregate MAE/RMSE in both validation and final test. Hour-level wins are not uniform: for example, the test lag-24 baseline is better at several early-morning hours (2–5 and 10–12), while HGB is much better around the morning/evening commute peaks and weekends. This is expected for a small four-month dataset.

## 10) Leakage/reproducibility test results

### Tests added

`tests/forecasting/test_phase5b_cutoff.py`:

- `test_synthetic_t_minus_1_exists_but_is_unavailable_under_cutoff` — synthetic continuous UTC spine with `demand(t-1)` present; verifies `lag_1` is removed, `lag_2 == demand(t-2)`, rolling 24/168 features use `shift(2)`, and mutating `demand(t-1)` leaves every conservative feature at `t` unchanged.

`tests/forecasting/test_phase5b.py`:

- `test_primary_feature_list_excludes_lag1_and_prohibited_fields` — primary feature list excludes `lag_1`, target components, flags, and identifiers; the contract rejects prohibited columns.
- `test_prediction_time_features_ignore_target_and_future_values` — mutating target/future/target-component/coverage values at and after `t` does not change features at `t`.
- `test_rolling_features_depend_only_on_data_through_t_minus_2` — exact 24/168 rolling means/std and `lag_2` checks.
- `test_ridge_imputer_and_scaler_are_fit_on_training_data_only` — imputer statistics and scaler means match training data only; adding validation values changes the combined median.
- `test_chronological_split_isolation` — 700/150/150 chronological split, no overlap, train max < validation min < test min.
- `test_complete_only_targets_are_used_for_training_masks` — boundary/unavailable/partial and NaN-target rows are excluded from training masks.
- `test_common_mask_comparison_uses_identical_timestamps_and_sign_convention` — common mask excludes all methods' missing predictions; both methods report the same n; positive improvement means the model is better; optimistic lag-1 is rejected from the common mask.
- `test_missing_hour_remains_missing_and_alignment_is_not_compressed` — missing target row remains, `lag_2` at `t+2` is NaN, rolling window with the missing hour is NaN, and a non-continuous spine is rejected.
- `test_deterministic_predictions_for_both_candidates` — repeated Ridge and HGB fits with identical seeds/params produce identical predictions.
- `test_final_test_requires_frozen_configuration` — final-test guard raises when no frozen configuration/model exists.

### Results

```bash
PYTHONPATH=src python -m pytest tests/forecasting/ -v
```

**25 collected, 25 passed, 0 failed, 0 skipped, exit code 0.**

```bash
PYTHONPATH=src python -m pytest tests/unit -q
```

**15 passed, exit code 0.**

`ruff` is not installed in the environment (`ruff` not found; `python3 -m ruff` reports no module), so `ruff check src tests` could not be run. Python compilation checks passed for all new/modified modules and scripts.

### Reproducibility commands and artifacts

Deterministic workflow from the repository root:

```bash
PYTHONPATH=src python3 scripts/train_forecasting_models.py search
PYTHONPATH=src python3 scripts/train_forecasting_models.py validate
PYTHONPATH=src python3 scripts/train_forecasting_models.py freeze
PYTHONPATH=src python3 scripts/train_forecasting_models.py final-test
```

Exit codes were **0** for all four stages. The final-test stage will refuse to rerun unless `--overwrite-final-test` is passed, and will refuse to run without a frozen config and matching dataset hash.

Local artifacts (all under gitignored `data/processed/phase5b/`):

```text
data/processed/phase5b/hyperparameter_search.json
data/processed/phase5b/validation_metrics.json
data/processed/phase5b/frozen_model_config.json
data/processed/phase5b/final_model.joblib
data/processed/phase5b/feature_list.json
data/processed/phase5b/final_test_metrics.json
```

Determinism controls: rows sorted by `hour_utc`; continuous-spine validation; fixed chronological splits; `random_state=42`; `n_jobs=1`; `TimeSeriesSplit` with no shuffle; explicit feature ordering; deterministic JSON serialization; dataset SHA-256 recorded in search/freeze artifacts and enforced at final-test. `scikit-learn>=1.8.0` was added to `pyproject.toml`; `joblib` is available through the scikit-learn dependency and is used to persist the model.

No generated data/artifacts were committed. No commit was created.

## 11) Known limitations

1. **Test split is not pristine.** It was inspected during Phase 5A/5A.1/5A.2/5A.3. Phase 5B did not tune or select on test, but the test metrics are not a fully untouched holdout.
2. **Ingestion latency is unmeasured.** The conservative primary model assumes the newest available demand is `t-2`. The optimistic lag-1 reference is reported separately and is non-operational; it must not be used as the primary claim.
3. **Complete-only primary population.** Boundary, partial, unavailable, and uncertain target hours are excluded from primary training/evaluation. This intentionally leaves 2,835 of 2,953 spine hours for primary use. Boundary/partial sensitivity analysis from Phase 5A remains separate.
4. **Outage effects on long rolling features.** The January 2026 outage makes `roll2_*_168` missing for 112 test rows. HGB handles them natively; Ridge imputes them. The final test common mask is 380 because `lag_24` is unavailable for 19 complete test targets.
5. **Small dataset.** Only four months of hourly data and a single seasonal window are available. The validation and test periods are short, and hour-level estimates have 15–18 observations per hour.
6. **Ridge performs poorly here.** With complete-case target filtering, median imputation, and the conservative feature set, Ridge is much worse than both HGB and lag-24. This is documented rather than hidden.
7. **Limited search.** The search is intentionally small: 7 Ridge configurations and 12 HGB configurations, with a single chronological CV strategy. A larger search could overfit the short validation window and was avoided.
8. **Historical features use observed values from all coverage statuses.** Coverage flags are never predictors, and missing hours remain NaN, but boundary/partial observed values can enter historical windows. If those observations are biased or truncated, historical features may carry that bias.
9. **No drift monitoring or deployment.** MLflow, FastAPI, Streamlit, Airflow, Docker, databases, and monitoring infrastructure were explicitly out of scope. The frozen joblib artifact is a local research artifact, not a production service.
10. **Environment path detail.** The shell has no `python` executable; the tests were run through a temporary external shim pointing to Python 3.13.3. No repository file was added for this.
11. **Ruff unavailable.** `ruff check src tests` could not be run because ruff is not installed. Python compilation and pytest checks passed.

## 12) Readiness recommendation for the next phase

**Recommendation: READY to proceed to Phase 6 planning, with the conservative Phase 5B model frozen.** Phase 5B is complete and stops here; no Phase 6 work was started.

Before any operational use or further modeling:

- Keep the `t-2` conservative cutoff unless actual ingestion latency is measured and documented.
- Keep `lag_1` out of the primary model; if lag-1 is ever used, label it optimistic and evaluate it separately.
- Preserve the frozen feature set, preprocessing, missing-data policy, and evaluation mask. Any change should be treated as a new model version and selected on validation, not test.
- Continue reporting the common-mask comparison against `lag_24` with explicit numerator/denominator coverage.
- Treat the test split as pre-inspected; for a genuinely untouched holdout, a future time window is needed.
- Investigate the January outage's effect on long rolling features and consider whether a sensitivity run excluding post-outage test hours is warranted.
- Measure ingestion latency before making any operational lag-1 claim.
- If weather is added in a later phase, join it on UTC and re-run leakage and cutoff tests; weather was not added in Phase 5B.

No weather, MLflow, FastAPI, Streamlit, Airflow, Docker, database, or unrelated infrastructure work was added. No commit was made. Phase 5B is the stopping point.
