# Dataset Evaluation

> **Phase 1** discovered candidate sources. **Phase 2** verified Citi Bike access, schema,
> and sample quality by inspecting the live data (see the Phase 2 section below). Every
> factual claim is marked with its **verification status**:
> **[verified P1]** = inspected in Phase 1; **[verified P2]** = inspected live in Phase 2
> (tools + commands listed); **[unverified]** = believed true but not confirmed;
> **[inferred]** = derived from the sample, not guaranteed for all files;
> **[assumed]** = a modelling/design assumption.

## Phase 2 — Verification results (Citi Bike access, schema, sample, weather)

### Access method

- `https://citibikenyc.com/system-data` → the documented download index
  `https://s3.amazonaws.com/tripdata/index.html` is **JavaScript-rendered** and returns no
  listing to a plain HTTP fetch. **[verified P2]**
- The underlying bucket exposes a standard **S3 ListObjectsV2 XML API**, which works:
  `GET https://tripdata.s3.amazonaws.com/?list-type=2` returned **`<KeyCount>178</KeyCount>`,
  `IsTruncated=false`**. **[verified P2]** — this is the reliable machine-readable index.
- Individual objects are downloadable directly, e.g.
  `https://tripdata.s3.amazonaws.com/202601-citibike-tripdata.zip`. **`Accept-Ranges: bytes`**
  is supported. **[verified P2]**

### Inventory (from the 178-key listing)

- **NYC monthly files** `YYYYMM-citibike-tripdata.zip`: **`202401` … `202609`**, i.e. 33
  monthly archives. Latest complete month at verification time (2026-10-19) = **`202609`**. **[verified P2]**
- **NYC yearly files** `YYYY-citibike-tripdata.zip`: **2013 … 2023**. **[verified P2]**
- **Jersey City files** `JC-YYYYMM-citibike-tripdata…`: **`JC-201509` … `JC-202609`** (133 files). **[verified P2]**

### Sizes (compressed, `Size` from listing)

- NYC monthly range ≈ **165 MB (202604) … 1051 MB (202606)**. **[verified P2]**
- Window totals: **12 months (`202510`–`202609`) = 8.27 GB**; 24 months = 17.39 GB; all 33
  months = 23.87 GB. **[verified P2]**
- **Anomaly [inferred]:** `202602` (237.6 MB) and `202604` (164.6 MB) are far smaller than
  the same month in the prior year (`202502` 396.0 MB, `202504` 727.2 MB). They are
  suspected partial/incomplete publications. Not proven; verify before using those months.
- JC files ≈ **0.1–4.4 MB** each (all 133 ≈ a few hundred MB). **[verified P2]**

### Sample inspected (the only archive downloaded in Phase 2)

- Object: `202601-citibike-tripdata.zip` (January 2026). **Compressed 353,829,139 bytes
  (353.8 MB); SHA-256 `8d579be9…71ae9755`.** **[verified P2]**
- Archive contains **two CSV members**: `202601-citibike-tripdata_1.csv` (194,843,701 B,
  exactly **1,000,000** rows) and `202601-citibike-tripdata_2.csv` (158,985,144 B, **816,391**
  rows). Total **1,816,391 rows**, uncompressed ≈ 353.8 MB. **[verified P2]**
  (Confirms the vendor note that high-volume months are split across multiple CSVs.)
- First row of `_1`: `85744AF35D7F2DF5,electric_bike,2026-01-02 05:36:24.539,…`. **[verified P2]**

### Schema (modern) — measured

Header (identical in both members): **[verified P2]**

```
ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,
end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual
```

- 13 columns; all read as text by default (dtypes must be cast explicitly). **[verified P2]**
- Timestamp format: **`YYYY-MM-DD HH:MM:SS.mmm`** — space-separated, milliseconds,
  **no timezone** (naive local time). **[verified P2]**
- This is the **modern schema**; it matches the vendor's current field list. **[verified P2]**
- `rideable_type` ∈ {`classic_bike`, `electric_bike`}; `member_casual` ∈ {`member`, `casual`}. **[verified P2]**

### Data-quality measurements (sample only — do not extrapolate to all files)

| Check | Result | Status |
|---|---|---|
| Rows | 1,816,391 | verified P2 |
| `started_at` range | 2025-12-30 23:30:09 … 2026-01-31 23:56:50 | verified P2 |
| `ended_at` range | 2026-01-01 00:00:00 … 2026-01-31 23:59:54 | verified P2 |
| Null `ride_id` / `rideable_type` / `started_at` / `ended_at` / `member_casual` | 0 | verified P2 |
| Null `start_station_id` / `name` / `start_lat` / `start_lng` | 1,334 (0.07%) | verified P2 |
| Null `end_station_id` | 8,799 (0.48%); `end_station_name` 7,100 (0.39%) | verified P2 |
| Null `end_lat` / `end_lng` | 8,731 (0.48%) | verified P2 |
| Duplicate `ride_id` | **0** | verified P2 |
| Negative durations | **0** | verified P2 |
| Duration ≤ 0 s | 0; **< 60 s = 3**; > 24 h = 658; max = 89,997.8 s | verified P2 |
| `rideable_type` | electric 1,317,343 (72.5%); classic 499,048 (27.5%) | verified P2 |
| `member_casual` | member 1,653,344 (91.0%); casual 163,047 (9.0%) | verified P2 |
| Distinct `start_station_id` / `end_station_id` | 2,207 / 2,272 | verified P2 |

**Interpretation [inferred]:** null end-station/coordinates ≈ trips not docked at a valid
station (lost/valet/abandoned) — a normal category, not necessarily an error. Null
start-station ≈ trips starting off-station. These must be *classified*, not blindly dropped.
The <60 s and >24 h rows contradict a strict reading of the vendor's "cleaned" claim, so
validation rules remain necessary. One month does **not** imply the whole history behaves
this way.

### Ingestion gotchas found (all verified P2)

1. **Yearly zips are nested containers**, not flat CSVs: `2019-…zip` contains
   `2019-citibike-tripdata/` with per-month subfolders; `2023-…zip` contains a
   **zip-within-a-zip** (`2023…/202302…zip`). ✔ verified. → Avoid pre-2024 yearly files;
   a window inside `2024+` monthly files avoids nested-archive handling entirely.
2. **Filename inconsistency:** JC objects mix `JC-YYYYMM-citibike-tripdata.csv.zip` and
   `JC-YYYYMM-citibike-tripdata.zip` (missing `.csv`). ✔ verified. → **Parse the S3 listing;
   never construct filenames.**
3. **Non-trip members inside archives:** the JC zip contained a `__MACOSX/._…` entry. ✔
   verified. → Select CSV members explicitly; skip hidden/`__MACOSX` entries.
4. **Quoting varies:** NYC headers are unquoted; JC headers are quoted (`"ride_id",…`). ✔
   verified. → Use a tolerant CSV reader.
5. **Station IDs are not all numeric:** NYC forms like `1234.56` / `5303.06_` (trailing
   underscore) and JC alphanumeric IDs like `HB202`. ✔ verified. → Treat IDs as **text**.
6. **Month boundaries are fuzzy:** the January file contains a few `started_at` in late
   December. ✔ verified. → Do not assume a monthly file maps 1:1 to a calendar month.
7. **Multi-file months:** high-volume months split into ≥2 CSVs (e.g. `_1`, `_2`). ✔ verified.

### Jersey City (lightweight alternative, same bucket)

- Same **modern 13-column schema**, quoted headers; `JC-202601…zip` = 8,672,671 B
  uncompressed, **40,044 rows** for January 2026. **[verified P2]**
- ~2 orders of magnitude smaller than NYC monthly files → a viable low-volume fallback,
  but it is a **different, smaller system** with simpler demand dynamics.

### Weather source — verified and a critical caveat

- One Open-Meteo `/v1/archive` request for NYC (40.71, −74.0) succeeded; hourly time series
  returned with variables `temperature_2m, precipitation, relative_humidity_2m,
  wind_speed_10m` and ISO-8601 `time` values. **[verified P2]**
- Response includes `timezone`, `timezone_abbreviation`, `utc_offset_seconds`, and resolves
  the coordinate to a nearby **grid cell** (returned lat/lon ≈ 40.74, −74.04). **[verified P2]**
- **Critical caveat [verified P2]:** with `timezone=America/New_York`, a **January 2026**
  request returned `timezone_abbreviation=GMT-4`, `utc_offset_seconds=-14400` — i.e. the
  **summer (EDT) offset applied to winter timestamps**. A July 2026 request returned the
  same −4. The offset is resolved **at request time** and held constant across the range
  (as the docs state). → Naive use of these "local" timestamps will misalign weather by one
  hour across DST.
  - **Mitigation:** request in **UTC** (`timezone=GMT`, the default) or with
    `timeformat=unixtime`, store absolute instants, and convert to `America/New_York` for
    the join using `zoneinfo`.
- **Licensing still needs manual review:** the site presents Non-Commercial / Commercial /
  Self-Hosted options; confirm terms before publishing derived weather data. **[unverified]**
- Historical availability: ERA5 is daily-updated with ~5-day delay; January 2026 was
  available at verification time. **[verified P2]**

### Phase 2 conclusion

**Citi Bike NYC remains the right choice.** Access is reliable via the S3 XML listing,
schema is identical across the 2024+ monthly files (single modern schema), the sample parses
cleanly, and quality is high enough to be interesting (null end-stations, odd durations,
multi-file months) without being pathological. The main cost is **volume**: choose a bounded
window and process month-by-month (see `architecture.md`). Weather is feasible but must be
joined on **UTC** to avoid the DST offset trap.

---

## Original Phase 1 evaluation (retained)

## Verification method

The following official pages were retrieved and read during Phase 1:

- UCI Machine Learning Repository, *Bike Sharing* dataset page — https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset **[verified]**
- Divvy System Data — https://divvybikes.com/system-data **[verified]**
- Capital Bikeshare System Data — https://capitalbikeshare.com/system-data **[verified]**
- Citi Bike System Data — https://citibikenyc.com/system-data **[verified]**
- Citi Bike Data Sharing Policy — https://www.citibikenyc.com/data-sharing-policy **[verified]**
- Open-Meteo Historical Weather API — https://open-meteo.com/en/docs/historical-weather-api **[verified]**

Download index pages (`*.s3.amazonaws.com/*/index.html`) are JavaScript-rendered and
returned no file listing to the fetcher, so **per-file sizes and exact available date
ranges from those buckets are [unverified]** here. Download formats below reflect the
static descriptions on the vendor pages plus **[assumption]** where noted.

---

## Candidate A — UCI *Bike Sharing* dataset (Capital Bikeshare, Washington D.C.)

- **Source URL:** https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset **[verified]**
- **System:** Capital Bikeshare (Washington D.C.). **[verified]** — page states it covers the
  "Capital bikeshare system".
- **Period:** years **2011–2012**. **[verified]**
- **Granularity:** **hourly** (`hour.csv`) and **daily** (`day.csv`). **[verified]**
- **Fields:** `instant, dteday, season, yr, mnth, hr, holiday, weekday, workingday,
  weathersit, temp, atemp, hum, windspeed, casual, registered, cnt`. **[verified]**
  - `cnt` = total rentals (casual + registered) → the target.
  - `weathersit`, `temp`, `atemp`, `hum`, `windspeed` → weather already included.
- **Weather included?** **Yes**, baked into the dataset. **[verified]**
- **License:** **CC BY 4.0**. **[verified]** (attribution required).
- **Format & size:** `hour.csv` **1.1 MB**, `day.csv` **56.2 KB**, `Readme.txt` **5.5 KB**;
  distributed as a **273.4 KB** zip. **[verified]**
- **Instances:** 17,389. **[verified]**
- **Target/role:** Regression; target = `cnt`. **[verified]**

**Advantages**
- Already hourly with weather, seasonality, holiday and calendar fields → fastest path to a
  defensible forecasting result.
- Tiny, clean, well documented; ideal for reproducibility and CI.
- Clear license (CC BY 4.0) and a citable DOI (`10.24432/C5W894`). **[verified]**

**Limitations**
- **Static** (2011–2012, DC). No incremental ingestion, no raw trip records, no station
  dimension → weak data-engineering story (little to clean/aggregate).
- Fixed historical window; cannot demonstrate scheduled/ongoing ingestion.
- Weather is pre-aggregated/normalised, so it does not exercise an external weather join.

**Suitability for a 6-week project:** Very high as a **fast, low-risk fallback or
cross-check**, but **weak as the primary source for a data-engineering portfolio** because
it removes most ingestion/transformation work.

---

## Candidate B — Citi Bike NYC trip data (recommended)

- **Source URL:** https://citibikenyc.com/system-data **[verified P1]**
- **Download location:** `https://s3.amazonaws.com/tripdata/index.html` (JS-rendered) but the
  bucket is accessible via **S3 ListObjectsV2 XML API** (`https://tripdata.s3.amazonaws.com/`)
  — parse the listing. **[verified P2]**
- **System:** Citi Bike NYC (operated by Lyft Bikes and Scooters, LLC). **[verified P1]**
- **Granularity:** **trip-level**, **monthly** downloadable files. For high-volume months
  files are split into ≥2 CSVs. **[verified P2]**
- **Current fields (modern, 2024+ monthly):** `ride_id, rideable_type, started_at, ended_at,
  start_station_name, start_station_id, end_station_name, end_station_id, start_lat, start_lng,
  end_lat, end_lng, member_casual` — 13 columns, space-separated `YYYY-MM-DD HH:MM:SS.mmm`
  timestamps, **no timezone** (local NY). **[verified P2]** (Measured from `202601` sample.)
- **Legacy schema (pre-2024):** different field set; yearly zips are **nested containers**
  (per-month subdirs and/or zip-within-zip in 2023) — do not assume flat files for legacy.
  **[verified P1, structure verified P2]**
- **Target derivation:** hourly demand = count of trips by `started_at` hour (system-wide,
  trip starts). **[verified by schema]** [inferred]
- **Weather included?** **No.** Needs Open-Meteo; see caveat below. **[verified P2]**
- **License:** "NYCBS Data Use Policy" (Data Sharing Policy). Permits non-commercial
  analysis/copy/modify; **prohibited** to host/stream/publish/distribute/sublicense/sell as a
  stand-alone dataset. No warranty. Do **not** commit raw data. **[verified P1]**
- **Format & size:** compressed CSV archives. Monthly NYC compressed ≈ 165–1051 MB; 12
  months (`202510–202609`) = **8.27 GB**; 24 months = **17.39 GB**; 33 months = **23.87 GB**.
  **[verified P2]** (`202602`, `202604` are suspected partial.) **[inferred for all]**
- **Real-time/streaming feed:** GBFS 2.3 feed exists. **[verified P1]**
- **History:** `202401`–`202609` present (33 months); pre-2024 are yearly zips. **[verified P2]**

**Advantages**
- Real **trip-level** data → genuine ingestion, de-duplication, station dimension, and
  hourly aggregation work: a strong data-engineering narrative.
- Rich fields incl. lat/long and ebike ("Rideable type") enabling a station dimension and
  richer analytics.
- Long, ongoing, monthly publication → supports incremental and scheduled ingestion.
- A **GBFS live feed** exists, enabling an optional near-real-time extension later.
- Broad, well-known system → recognisable in a portfolio.

**Limitations**
- Data volume is large; full history is not appropriate for a single dev in 6 weeks →
  must scope to a bounded window. **[assumption]**
- Two epoch schemas (new vs. legacy) → schema-drift handling required. **[verified]**
- **Weather must be sourced and joined separately** (added complexity, but a plus for the
  portfolio).
- License forbids redistribution as a stand-alone dataset → do **not** commit raw data or
  publish a downloadable mirror; document how to fetch it. **[verified]**

**Suitability for a 6-week project:** **High**, provided the window is bounded and weather
enrichment is treated as a first-class step.

---

## Candidate C — Divvy (Chicago) trip data

- **Source URL:** https://divvybikes.com/system-data **[verified]**
- **Download:** https://divvy-tripdata.s3.amazonaws.com/index.html **[verified as the
  documented location]**.
- **System:** Divvy, Chicago (operated by Lyft). **[verified]**
- **Granularity:** **trip-level**, **monthly** release schedule. **[verified]**
- **Fields:** `Trip start day and time, Trip end day and time, Trip start station,
  Trip end station, Rider type (Member, Single Ride, Day Pass)`. **[verified]**
  (Page describes these; exact column names/types in the CSVs: **[unverified]**.)
- **Processing note:** vendor states the data **removes staff/service trips and trips under
  60 seconds**. **[verified]**
- **Weather included?** **No.** **[verified]** (not mentioned).
- **License:** "Divvy Data License Agreement" — page links to
  https://www.divvybikes.com/data-license-agreement; terms not read in Phase 1 → **[unverified]**.
- **Format & size:** CSV archives from the S3 index; exact sizes **[unverified]**.
- **Real-time feed:** GBFS at https://gbfs.divvybikes.com/gbfs/2.3/gbfs.json. **[verified]**

**Advantages**
- Same structural strengths as Citi Bike (trip-level, monthly, GBFS), slightly smaller city.
- Documents a data-cleaning policy (staff trips / <60s removed), useful for validation rules.

**Limitations**
- Fewer published fields than Citi Bike (no lat/long or rideable type listed on the page)
  → weaker station geometry and less feature richness. **[verified on the page's field list]**
- License terms not confirmed during Phase 1. **[unverified]**

**Suitability for a 6-week project:** **High as a substitute** for Citi Bike; the choice
between B and C is mostly about field richness and data volume.

> Other systems exist (e.g., London Santander Cycles/TfL, other open-data portals) but
> were not inspected in Phase 1 and are therefore not evaluated here.

---

## Weather source (needed only if a candidate lacks weather)

- **Source:** Open-Meteo **Historical Weather API**. URL:
  https://open-meteo.com/en/docs/historical-weather-api **[verified]**
- **Endpoint:** `/v1/archive`. **[verified]**
- **Coverage:** ERA5 reanalysis **hourly from 1940**; ERA5-Land from 1950; higher-resolution
  models from 2017. **[verified]**
- **Variables relevant here:** `temperature_2m, relative_humidity_2m, apparent_temperature,
  precipitation, rain, snowfall, cloud_cover, wind_speed_10m, weather_code`. **[verified]**
- **Access:** free, no API key for non-commercial use; a key is required only for
  commercial reserved resources. **[verified]**
- **License nuance:** the site presents "Non-Commercial / Commercial / Self-Hosted" usage
  options; **confirm the exact licence terms** for your use case before publishing derived
  weather data. **[verify]**
- **Alternative (not verified in Phase 1):** NOAA Integrated Surface Database (ISD) as a
  fully open fallback. **[assumption]** — verify if you prefer a pure public-domain source.

---

## Recommendation

**Primary dataset: Citi Bike NYC trip data (Candidate B)**. Use **`2024+` monthly archives**
(avoid yearly nested zips) and scope to a **bounded window**. Proposed defaults:

- **Dev/CI slice:** 1 month (fast). 
- **MVP window:** **6–12 months** (configurable). Recommend **12 months** if disk/time allow
  (covers annual seasonality) but start at **6 months**. 12 months compressed = 8.27 GB
  (range `202510–202609`); 6 months = ~4–5 GB. **[verified P2, assumed for UX]**
- **Process month-by-month**: download → stream-parse → aggregate hourly counts + write
  landing (Parquet) → load conformed trip rows into Postgres (or keep trips in Parquet) →
  build marts. **[assumed design decision]**
- Weather enrichment via **Open-Meteo Historical API** (join on UTC; use up-to-origin
  weather only). **[verified P2 + design guard]**

**Why B over A and C:**

1. **Best data-engineering story.** Trip-level with multi-file months, two schema epochs
   (legacy/nested vs modern monthly), null handling, duration outliers, and aggregation to
   hourly demand. **[verified P2]**
2. **Richer fields than C.** Lat/long, rideable type → station dimension and richer
   analytics. **[verified P1/P2]**
3. **Ongoing publication + real access.** Listing parsable; recent months present; GBFS
   available. **[verified P2]**
4. **Clear license** with explicit redistribution restriction → no raw data in repo.
   **[verified P1]**

**Why not A as primary:** too easy (already hourly+weather), little ingestion/cleaning.

**Fallbacks:**

- **Jersey City (JC)** monthly files: tiny (0.1–4.4 MB/month), same modern schema — a
  very light fallback for CI if NYC volume is slow. Trade-off: smaller, different system.
  **[verified P2]**
- **UCI Bike Sharing (A):** ideal safety fallback if raw-ingestion scope must be cut. Keep
  same forecasting surface. **[verified P1]**

### Contingency triggers

- If downloads/processing prove too heavy: reduce window to 6 (or 3) months; use dev slice
  for tests; or switch to JC.
- If weather joining blocks progress: ship calendar features only, add weather later
  (and document DST/UTC join).
- If raw ingestion overruns: fallback to UCI (A) while keeping analytics + forecasting +
  MLOps surface.

### Compliance notes (binding constraints)

- **Do not** commit raw Citi Bike data; forbids redistribution as a stand-alone dataset.
  **[verified P1]** Fetch via S3 listing, store in `data/`/landing, git-ignore.
- Attribute UCI if used (CC BY 4.0). **[verified P1]**
- **Confirm Open-Meteo licence terms** before publishing derived weather datasets
  (Non-Commercial/Commercial variants). **[unverified]**
- Name/structure gotchas: parse listing (not hardcoded), skip `__MACOSX/._…`, handle quoted
  vs unquoted headers, station IDs may have suffixes (e.g. `5303.06_`) or be alphanumeric
  (JC), multi-file months, fuzzy month boundaries, nested yearly zips. **[verified P2]**
