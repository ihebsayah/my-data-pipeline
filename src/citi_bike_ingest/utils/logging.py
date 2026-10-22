from __future__ import annotations

import logging
import sys
from pathlib import Path

from rich.logging import RichHandler


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).resolve().parents[2]


def get_data_dir() -> Path:
    """Get data directory."""
    return get_project_root() / "data"


def get_cache_dir() -> Path:
    """Get cache directory."""
    return get_project_root() / "data" / "cache"
