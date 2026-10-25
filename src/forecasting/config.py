from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pytz


@dataclass(frozen=True)
class ForecastingConfig:
    """Configuration for Phase 5A forecasting."""

    # Database connection (uses same defaults as app)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "citi_bike"
    postgres_password: str = "citi_bike_dev"
    postgres_db: str = "citi_bike"

    # Target and horizon
    target_col: str = "rides_total"
    horizon_hours: int = 1  # one-hour-ahead
    prediction_timestamp_is_start_of_target: bool = True

    # Timezone for calendar features
    local_tz: str = "America/New_York"

    # Ingestion latency assumption (configurable)
    previous_hour_available_at_prediction: bool = True
    previous_hour_latency_hours: float = 0.0
    ingestion_latency_assumption_note: str = (
        "Assumed previous hour observed demand available at prediction time "
        "(ingestion/aggregation latency unknown; configurable)."
    )
    # Information cutoff configuration (for operational availability)
    # Features requiring data newer than (prediction_time - cutoff_hours) are forbidden
    information_cutoff_hours: float = 0.0  # 0 means "strictly before prediction time" baseline
    # Conservative mode: do not require/use lag-1 at prediction time
    conservative_no_lag1: bool = False

    # Chronological split
    train_start: Optional[datetime] = None
    train_end: Optional[datetime] = None
    val_start: Optional[datetime] = None
    val_end: Optional[datetime] = None
    test_start: Optional[datetime] = None
    test_end: Optional[datetime] = None

    # Eligibility
    # Primary evaluation population (Phase 5A.1): use only complete coverage
    primary_evaluation_eligible_statuses: tuple[str, ...] = ("complete",)
    # Sensitivity analyses preserve boundary/partial
    sensitivity_eligible_statuses: tuple[str, ...] = ("complete", "boundary", "partial")
    eligible_coverage_statuses: tuple[str, ...] = ("complete",)  # primary evaluation default
    # partial with gap_resume is in demand mart and eligible per 4C guidance
    exclude_uncertain: bool = True
    exclude_unavailable: bool = True
    # boundary hours are truncated - included only in sensitivity analyses

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def get_local_tz():
    return pytz.timezone("America/New_York")
