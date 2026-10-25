from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ForecastingContract:
    """Formal forecasting contract for Phase 5A."""

    target: str = "rides_total"
    horizon_hours: int = 1
    prediction_timestamp_definition: str = "beginning of the target UTC hour"
    available_inputs: str = "information known strictly before the prediction timestamp"
    canonical_time_index: str = "hour_utc"
    calendar_features_from: str = "America/New_York local time"

    # Availability assumptions
    previous_hour_observed_available_at_prediction: bool = True
    ingestion_latency_known: bool = False
    ingestion_latency_assumption: str = (
        "If ingestion latency is unknown, we assume the previous hour's observed "
        "demand becomes available at/ before prediction time for naive baselines; "
        "this is configurable via ForecastingConfig."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CONTRACT = ForecastingContract()
