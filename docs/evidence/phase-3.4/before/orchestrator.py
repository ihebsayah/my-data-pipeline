from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from citi_bike_ingest.config.settings import Settings, get_settings
from citi_bike_ingest.db.loader import DatabaseLoader, LoadStats
from citi_bike_ingest.ingest.downloader import Downloader
from citi_bike_ingest.ingest.parser import ZipCSVParser
from citi_bike_ingest.ingest.s3_listing import CitiBikeS3Listing
from citi_bike_ingest.utils.files import ensure_dir
from citi_bike_ingest.utils.hash import compute_sha256
from citi_bike_ingest.utils.logging import get_cache_dir, get_data_dir, setup_logging

logger = logging.getLogger(__name__)


class IngestionOrchestrator:
    """Orchestrate ingestion of one month."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.project_root = Path(__file__).resolve().parents[2]
        self.cache_dir = self.project_root / self.settings.cache_dir.lstrip("./")
        self.data_dir = self.project_root / self.settings.data_dir.lstrip("./")
        ensure_dir(self.cache_dir)
        ensure_dir(self.data_dir)
        setup_logging(self.settings.log_level)

    def ingest_month(self, month: str, dry_run: bool = False) -> dict:
        """Ingest one month's data."""
        start_time = time.time()
        logger.info(f"Starting ingestion for month {month} (dry_run={dry_run})")

        # Fetch listing
        listing = CitiBikeS3Listing()
        objects = listing.fetch_listing()
        logger.info(f"Fetched {len(objects)} objects from S3 listing")

        # Find NYC archive
        obj = listing.find_nyc_month(month, objects)
        if not obj:
            raise ValueError(f"No NYC archive found for month {month}")
        logger.info(f"Selected archive: {obj.key} ({obj.size} bytes)")

        if dry_run:
            return {
                "month": month,
                "archive_key": obj.key,
                "archive_size": obj.size,
                "dry_run": True,
            }

        # Download
        downloader = Downloader(self.cache_dir, self.settings.retry_attempts, self.settings.retry_delay)
        url = f"{listing.BUCKET_URL}{obj.key}"
        archive_path = downloader.download(obj.key, url, obj.size)
        logger.info(f"Archive ready: {archive_path}")

        checksum = compute_sha256(archive_path)

        # Parse
        parser = ZipCSVParser(archive_path)
        members = parser.get_csv_members()
        logger.info(f"CSV members: {members}")

        # DB
        loader = DatabaseLoader(self.settings)
        loader.connect()
        loader.ensure_schema()
        manifest_id = loader.create_manifest(obj.key, month, checksum, obj.size, str(archive_path))

        total_read = 0
        total_accepted = 0
        total_rejected = 0
        total_dups = 0

        try:
            for batch in parser.iterate_rows(self.settings.batch_size):
                total_read += len(batch)
                stats: LoadStats = loader.load_batch(batch, obj.key)
                total_accepted += stats.rows_inserted
                total_rejected += stats.rows_rejected
                total_dups += stats.duplicates_skipped
                logger.info(
                    f"Batch: inserted={stats.rows_inserted}, rejected={stats.rows_rejected}, dups={stats.duplicates_skipped}"
                )
            loader.complete_manifest(
                manifest_id, total_read, total_accepted, total_rejected, total_dups, None
            )
        except Exception as e:
            logger.error(f"Ingestion failed: {e}")
            loader.complete_manifest(
                manifest_id, total_read, total_accepted, total_rejected, total_dups, str(e)
            )
            loader.close()
            raise
        finally:
            loader.close()

        elapsed = time.time() - start_time
        result = {
            "month": month,
            "archive_key": obj.key,
            "archive_size": obj.size,
            "archive_path": str(archive_path),
            "checksum_sha256": checksum,
            "csv_members": members,
            "rows_read": total_read,
            "rows_accepted": total_accepted,
            "rows_rejected": total_rejected,
            "duplicates_detected": total_dups,
            "elapsed_seconds": round(elapsed, 2),
        }
        logger.info(f"Ingestion complete: {result}")
        return result
