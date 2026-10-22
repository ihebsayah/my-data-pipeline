from __future__ import annotations

from citi_bike_ingest.ingest.s3_listing import CitiBikeS3Listing, S3Object


def test_find_nyc_month() -> None:
    objects = [
        S3Object(key="JC-202601-citibike-tripdata.zip", size=1000, last_modified="2026-01-01"),
        S3Object(key="202601-citibike-tripdata.zip", size=2000, last_modified="2026-01-01"),
        S3Object(key="202602-citibike-tripdata.zip", size=2000, last_modified="2026-01-01"),
    ]
    listing = CitiBikeS3Listing()
    found = listing.find_nyc_month("202601", objects)
    assert found is not None
    assert found.key == "202601-citibike-tripdata.zip"
    assert found.size == 2000


def test_ignore_jc() -> None:
    objects = [
        S3Object(key="JC-202601-citibike-tripdata.zip", size=1000, last_modified="2026-01-01"),
    ]
    listing = CitiBikeS3Listing()
    found = listing.find_nyc_month("202601", objects)
    assert found is None


def test_fuzzy_name() -> None:
    objects = [
        S3Object(key="202601-citibike-tripdata.csv.zip", size=1000, last_modified="2026-01-01"),
    ]
    listing = CitiBikeS3Listing()
    found = listing.find_nyc_month("202601", objects)
    assert found is not None
    assert found.key == "202601-citibike-tripdata.csv.zip"
