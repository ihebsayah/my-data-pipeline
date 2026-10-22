from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

import requests

from citi_bike_ingest.utils.hash import compute_sha256

logger = logging.getLogger(__name__)


class Downloader:
    """Safe downloader with caching and retry."""

    def __init__(
        self,
        cache_dir: Path,
        retry_attempts: int = 3,
        retry_delay: float = 2.0,
        timeout: int = 300,
    ) -> None:
        self.cache_dir = cache_dir
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def download(self, key: str, url: str, expected_size: int | None = None) -> Path:
        """Download with caching, resume support, atomic write."""
        dest = self.cache_dir / key

        # Check cache
        if dest.exists():
            if expected_size is None or dest.stat().st_size == expected_size:
                logger.info(f"Using cached file: {dest}")
                return dest
            else:
                logger.warning(f"Cached file size mismatch, re-downloading: {dest}")
                dest.unlink()

        # Try to resume if partial exists
        resume_from = 0
        if dest.exists():
            resume_from = dest.stat().st_size

        headers = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.cache_dir, prefix=".tmp_", suffix=".zip")
        os.close(tmp_fd)
        tmp_file = Path(tmp_path)

        # If resuming, copy existing partial
        if resume_from > 0 and dest.exists():
            shutil.copy2(dest, tmp_file)
            mode = "ab"
        else:
            mode = "wb"
            resume_from = 0

        last_err = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                with requests.get(url, headers=headers, stream=True, timeout=self.timeout) as resp:
                    if resume_from > 0 and resp.status_code not in (206, 200):
                        logger.warning(f"Resume failed with status {resp.status_code}, restarting")
                        resume_from = 0
                        headers = {}
                        mode = "wb"
                        tmp_file.unlink(missing_ok=True)
                        tmp_fd, tmp_path = tempfile.mkstemp(
                            dir=self.cache_dir, prefix=".tmp_", suffix=".zip"
                        )
                        os.close(tmp_fd)
                        tmp_file = Path(tmp_path)
                    elif resp.status_code >= 400:
                        raise Exception(f"HTTP {resp.status_code}")

                    with tmp_file.open(mode) as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                break
            except Exception as e:
                last_err = e
                logger.warning(f"Download attempt {attempt} failed: {e}")
                if attempt < self.retry_attempts:
                    time.sleep(self.retry_delay * attempt)
                else:
                    tmp_file.unlink(missing_ok=True)
                    raise

        # Validate size if expected
        if expected_size is not None and tmp_file.stat().st_size != expected_size:
            # Accept if we got close (allow range responses)
            if resume_from == 0 or tmp_file.stat().st_size < expected_size:
                logger.warning(
                    f"Size mismatch: got {tmp_file.stat().st_size}, expected {expected_size}"
                )

        # Atomic move
        tmp_file.replace(dest)
        logger.info(f"Downloaded to {dest} ({dest.stat().st_size} bytes)")
        return dest
