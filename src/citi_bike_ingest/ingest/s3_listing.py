from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class S3Object:
    """S3 object metadata."""

    key: str
    size: int
    last_modified: str


class CitiBikeS3Listing:
    """Citi Bike S3 listing parser."""

    BUCKET_URL = "https://tripdata.s3.amazonaws.com/"
    NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

    def __init__(self, timeout: int = 60) -> None:
        self.timeout = timeout

    def fetch_listing(self) -> List[S3Object]:
        """Fetch full listing with pagination."""
        objects: List[S3Object] = []
        continuation_token: Optional[str] = None

        while True:
            params = {"list-type": "2", "max-keys": "1000"}
            if continuation_token:
                params["continuation-token"] = continuation_token

            try:
                response = requests.get(self.BUCKET_URL, params=params, timeout=self.timeout)
                response.raise_for_status()
                root = ET.fromstring(response.content)
            except Exception as e:
                logger.error(f"Failed to fetch S3 listing: {e}")
                raise

            # Try with namespace first
            for contents in root.findall("s3:Contents", self.NS):
                key_elem = contents.find("s3:Key", self.NS)
                size_elem = contents.find("s3:Size", self.NS)
                lm_elem = contents.find("s3:LastModified", self.NS)
                if key_elem is not None and size_elem is not None and lm_elem is not None:
                    objects.append(
                        S3Object(
                            key=key_elem.text or "",
                            size=int(size_elem.text or "0"),
                            last_modified=lm_elem.text or "",
                        )
                    )
            # Try without namespace
            if not objects or len(objects) == 0:
                for contents in root.findall("Contents"):
                    key_elem = contents.find("Key")
                    size_elem = contents.find("Size")
                    lm_elem = contents.find("LastModified")
                    if key_elem is not None and size_elem is not None and lm_elem is not None:
                        objects.append(
                            S3Object(
                                key=key_elem.text or "",
                                size=int(size_elem.text or "0"),
                                last_modified=lm_elem.text or "",
                            )
                        )
                # Clear and re-fetch properly
                if objects:
                    pass

            is_truncated = root.find("s3:IsTruncated", self.NS)
            if is_truncated is None:
                is_truncated = root.find("IsTruncated")
            if is_truncated is not None and is_truncated.text == "true":
                token_elem = root.find("s3:NextContinuationToken", self.NS)
                if token_elem is None:
                    token_elem = root.find("NextContinuationToken")
                if token_elem is not None and token_elem.text:
                    continuation_token = token_elem.text
                    continue
            break

        return objects

    def find_nyc_month(self, month: str, objects: List[S3Object]) -> Optional[S3Object]:
        """Find NYC monthly archive for given month (YYYYMM)."""
        candidates = []
        for obj in objects:
            key = obj.key
            # NYC monthly: YYYYMM-citibike-tripdata.zip or .csv.zip
            # Must not start with JC-
            if key.startswith("JC-"):
                continue
            if key.startswith(f"{month}-citibike-tripdata"):
                if key.endswith(".zip"):
                    candidates.append(obj)
        if not candidates:
            return None
        # Prefer exact match without extra .csv in middle if both exist
        for c in candidates:
            if c.key == f"{month}-citibike-tripdata.zip":
                return c
        return candidates[0]
