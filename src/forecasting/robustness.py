from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .features import (
    PRIMARY_CUTOFF_HOURS,
    PRIMARY_LAG_HOURS,
    PRIMARY_ROLL_WINDOWS,
)
from .modeling import TARGET_COL, improvement_vs_baseline, regression_metrics

VOLUME_QUARTILE_LABELS = ("Q1", "Q2", "Q3", "Q4")
ROLL_STATS = ("mean", "std", "min", "max")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_manifest(directory: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(directory)
    if not root.exists():
        raise FileNotFoundError(f"Artifact directory not found: {root}")
    manifest: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = str(path.relative_to(root))
        manifest[relative] = {
            "sha256": file_sha256(path),
            "bytes": int(path.stat().st_size),
        }
    return manifest


def manifest_changed(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    before_keys = set(before)
    after_keys = set(after)
    changed = sorted(
        key
        for key in before_keys & after_keys
        if before[key].get("sha256") != after[key].get("sha256")
        or before[key].get("bytes") != after[key].get("bytes")
    )
    return {
        "unchanged": bool(before == after),
        "added": sorted(after_keys - before_keys),
        "removed": sorted(before_keys - after_keys),
        "changed": changed,
    }


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def compare_metrics(
    frame: pd.DataFrame,
    predictions: Mapping[str, pd.Series],
    mask: pd.Series,
    model_name: str = "frozen_hgb",
    baseline_name: str = "lag_24",
) -> dict[str, Any]:
    if mask.sum() == 0:
        return {
            "n": 0,
            "methods": {
                name: {"mae": None, "rmse": None, "n": 0}
                for name in predictions
            },
            f"improvement_{model_name}_vs_{baseline_name}": None,
        }

    y_true = frame.loc[mask, TARGET_COL]
    methods: dict[str, dict[str, Any]] = {}
    for name, prediction in predictions.items():
        metrics = regression_metrics(y_true, prediction.loc[mask])
        methods[name] = {
            "mae": _finite_or_none(metrics["mae"]),
            "rmse": _finite_or_none(metrics["rmse"]),
            "n": int(metrics["n"]),
        }

    improvement = None
    if model_name in methods and baseline_name in methods:
        if methods[model_name]["n"] and methods[baseline_name]["n"]:
            improvement = improvement_vs_baseline(
                methods[model_name], methods[baseline_name]
            )
    return {
        "n": int(mask.sum()),
        "methods": methods,
        f"improvement_{model_name}_vs_{baseline_name}": improvement,
    }


def grouped_metrics(
    frame: pd.DataFrame,
    predictions: Mapping[str, pd.Series],
    base_mask: pd.Series,
    labels: pd.Series,
    order: Sequence[Any],
    model_name: str = "frozen_hgb",
    baseline_name: str = "lag_24",
) -> list[dict[str, Any]]:
    if not labels.index.equals(frame.index):
        raise ValueError("Segment labels are not aligned with the frame")

    rows: list[dict[str, Any]] = []
    for label in order:
        group_mask = base_mask & labels.eq(label).fillna(False)
        payload = compare_metrics(
            frame,
            predictions,
            group_mask,
            model_name=model_name,
            baseline_name=baseline_name,
        )
        rows.append(
            {
                "segment": str(label),
                "n": payload["n"],
                "methods": payload["methods"],
                f"improvement_{model_name}_vs_{baseline_name}": payload[
                    f"improvement_{model_name}_vs_{baseline_name}"
                ],
            }
        )
    return rows


def volume_quartile_edges(
    frame: pd.DataFrame, train_mask: pd.Series
) -> dict[str, Any]:
    train_demand = frame.loc[train_mask, TARGET_COL].dropna().astype(float)
    if train_demand.empty:
        raise ValueError("Cannot derive volume quartiles from an empty train mask")
    q1, q2, q3 = train_demand.quantile([0.25, 0.5, 0.75]).tolist()
    return {
        "source_split": "train_complete",
        "n_source": int(len(train_demand)),
        "q1_max": float(q1),
        "q2_max": float(q2),
        "q3_max": float(q3),
        "labels": {
            "Q1": f"rides_total <= {q1:.0f}",
            "Q2": f"{q1:.0f} < rides_total <= {q2:.0f}",
            "Q3": f"{q2:.0f} < rides_total <= {q3:.0f}",
            "Q4": f"rides_total > {q3:.0f}",
        },
    }


def assign_volume_quartiles(
    frame: pd.DataFrame, edges: Mapping[str, Any]
) -> pd.Series:
    bins = [-np.inf, edges["q1_max"], edges["q2_max"], edges["q3_max"], np.inf]
    return pd.cut(
        frame[TARGET_COL].astype(float),
        bins=bins,
        labels=list(VOLUME_QUARTILE_LABELS),
        include_lowest=True,
        right=True,
    ).astype("object")


def outage_bounds(frame: pd.DataFrame) -> dict[str, Any] | None:
    unavailable = frame.loc[
        frame["coverage_status"].eq("unavailable"), "hour_utc"
    ]
    if unavailable.empty:
        return None
    start = unavailable.min()
    end_inclusive = unavailable.max()
    return {
        "start_utc": str(start),
        "last_unavailable_utc": str(end_inclusive),
        "end_utc_exclusive": str(end_inclusive + pd.Timedelta(hours=1)),
        "n_unavailable_hours": int(len(unavailable)),
    }


def outage_labels(frame: pd.DataFrame, bounds: Mapping[str, Any]) -> pd.Series:
    start = pd.Timestamp(bounds["start_utc"])
    labels = pd.Series("pre_outage", index=frame.index, dtype=object)
    labels.loc[frame["hour_utc"] >= start] = "post_outage"
    return labels


def rolling_history_availability(frame: pd.DataFrame, window: int) -> pd.Series:
    columns = [f"roll2_{stat}_{window}" for stat in ROLL_STATS]
    present = frame[columns].notna().all(axis=1)
    return present.map(
        {
            True: f"all_{window}_history_present",
            False: f"any_{window}_history_missing",
        }
    )


def _lag_affected(
    history_source: pd.Series, frame: pd.DataFrame, lag: int
) -> pd.Series:
    source = history_source.shift(lag, fill_value=False).astype(bool)
    return source & frame[f"lag_{lag}"].notna()


def _roll_affected(
    history_source: pd.Series,
    frame: pd.DataFrame,
    window: int,
    stat: str,
) -> pd.Series:
    source = (
        history_source.astype(float)
        .shift(PRIMARY_CUTOFF_HOURS)
        .rolling(window=window, min_periods=1)
        .max()
        .fillna(0.0)
        > 0.0
    )
    return source.astype(bool) & frame[f"roll2_{stat}_{window}"].notna()


def _affected_for_source(
    frame: pd.DataFrame, history_source: pd.Series
) -> tuple[pd.Series, dict[str, int]]:
    affected = pd.Series(False, index=frame.index)
    feature_counts: dict[str, int] = {}

    for lag in PRIMARY_LAG_HOURS:
        column = f"lag_{lag}"
        feature_affected = _lag_affected(history_source, frame, lag)
        feature_counts[column] = int(feature_affected.sum())
        affected |= feature_affected

    for window in PRIMARY_ROLL_WINDOWS:
        for stat in ROLL_STATS:
            column = f"roll2_{stat}_{window}"
            feature_affected = _roll_affected(
                history_source, frame, window, stat
            )
            feature_counts[column] = int(feature_affected.sum())
            affected |= feature_affected

    return affected, feature_counts


def boundary_partial_impact(frame: pd.DataFrame) -> dict[str, Any]:
    """Which prediction timestamps consume boundary/partial historical hours?"""
    status = frame["coverage_status"]
    boundary = status.eq("boundary")
    partial = status.eq("partial")
    boundary_or_partial = boundary | partial

    affected, feature_counts = _affected_for_source(
        frame, boundary_or_partial
    )
    boundary_affected, _ = _affected_for_source(frame, boundary)
    partial_affected, _ = _affected_for_source(frame, partial)

    source_targets = frame.loc[
        boundary_or_partial, TARGET_COL
    ].dropna()
    return {
        "source_rows": {
            "boundary": int(boundary.sum()),
            "partial": int(partial.sum()),
            "boundary_or_partial": int(boundary_or_partial.sum()),
            "boundary_or_partial_zero_targets": int(
                (source_targets == 0).sum()
            ),
        },
        "affected_any_historical_feature": affected,
        "affected_boundary_only": boundary_affected,
        "affected_partial_only": partial_affected,
        "affected_feature_counts_all_rows": feature_counts,
    }


def affected_counts_by_split(
    affected: pd.Series, masks: Mapping[str, pd.Series]
) -> dict[str, dict[str, int]]:
    payload: dict[str, dict[str, int]] = {}
    for split in ("train", "validation", "test"):
        complete_mask = masks[f"{split}_complete"]
        payload[split] = {
            "n_complete": int(complete_mask.sum()),
            "n_affected": int((complete_mask & affected).sum()),
            "n_unaffected": int((complete_mask & ~affected).sum()),
        }
    complete_mask = masks["complete_target"]
    payload["all_complete"] = {
        "n_complete": int(complete_mask.sum()),
        "n_affected": int((complete_mask & affected).sum()),
        "n_unaffected": int((complete_mask & ~affected).sum()),
    }
    return payload


def mask_boundary_partial_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy whose boundary/partial demand never enters lag/rolling history."""
    masked = frame.copy()
    boundary_or_partial = masked["coverage_status"].isin(["boundary", "partial"])
    masked.loc[boundary_or_partial, TARGET_COL] = np.nan
    return masked


def segment_payload(
    frame: pd.DataFrame,
    predictions: Mapping[str, pd.Series],
    split_mask: pd.Series,
    split_name: str,
    volume_edges: Mapping[str, Any],
    affected: pd.Series,
    outage: Mapping[str, Any] | None = None,
    in_sample: bool = False,
) -> dict[str, Any]:
    base_mask = (
        split_mask
        & frame["coverage_status"].eq("complete")
        & frame[TARGET_COL].notna()
    )
    for prediction in predictions.values():
        base_mask &= prediction.notna()
    base_mask = base_mask.fillna(False).astype(bool)

    overall = compare_metrics(frame, predictions, base_mask)
    weekday_labels = frame["is_weekend_local"].map(
        {0: "weekday", 1: "weekend"}
    )
    hour_labels = frame["hour_local"].astype(int)
    hours = sorted(frame.loc[base_mask, "hour_local"].dropna().unique().tolist())
    month_labels = frame["month"].astype(int)
    months = sorted(frame.loc[base_mask, "month"].dropna().unique().tolist())
    volume_labels = assign_volume_quartiles(frame, volume_edges)
    affected_labels = affected.map({True: "affected", False: "unaffected"})

    n_complete_coverage = int(
        (
            split_mask
            & frame["coverage_status"].eq("complete")
            & frame[TARGET_COL].notna()
        ).sum()
    )
    payload: dict[str, Any] = {
        "split": split_name,
        "in_sample": bool(in_sample),
        "n_spine_hours": int(split_mask.sum()),
        "n_target_notna": int(
            (split_mask & frame[TARGET_COL].notna()).sum()
        ),
        "n_complete_coverage_targets": n_complete_coverage,
        "n_common_mask": int(base_mask.sum()),
        "overall": overall,
        "by_weekday": grouped_metrics(
            frame,
            predictions,
            base_mask,
            weekday_labels,
            ["weekday", "weekend"],
        ),
        "by_hour": grouped_metrics(
            frame, predictions, base_mask, hour_labels, hours
        ),
        "by_month": grouped_metrics(
            frame, predictions, base_mask, month_labels, months
        ),
        "by_volume_quartile": grouped_metrics(
            frame,
            predictions,
            base_mask,
            volume_labels,
            list(VOLUME_QUARTILE_LABELS),
        ),
        "by_roll168_availability": grouped_metrics(
            frame,
            predictions,
            base_mask,
            rolling_history_availability(frame, 168),
            [
                "all_168_history_present",
                "any_168_history_missing",
            ],
        ),
        "by_roll24_availability": grouped_metrics(
            frame,
            predictions,
            base_mask,
            rolling_history_availability(frame, 24),
            [
                "all_24_history_present",
                "any_24_history_missing",
            ],
        ),
        "by_boundary_partial_history": grouped_metrics(
            frame,
            predictions,
            base_mask,
            affected_labels,
            ["unaffected", "affected"],
        ),
    }

    if outage is not None:
        payload["outage"] = {
            **dict(outage),
            "by_period": grouped_metrics(
                frame,
                predictions,
                base_mask,
                outage_labels(frame, outage),
                ["pre_outage", "post_outage"],
            ),
        }

    return payload
