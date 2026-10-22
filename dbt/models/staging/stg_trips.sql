with source as (

    select * from {{ source('raw', 'trips') }}

),

localized as (

    select
        ride_id,
        rideable_type,
        started_at_raw,
        ended_at_raw,
        {{ ny_local_ts('started_at_raw') }} as started_at_local,
        {{ ny_local_ts('ended_at_raw') }} as ended_at_local,
        nullif(btrim(start_station_id), '') as start_station_id,
        nullif(btrim(start_station_name), '') as start_station_name,
        nullif(btrim(end_station_id), '') as end_station_id,
        nullif(btrim(end_station_name), '') as end_station_name,
        start_lat,
        start_lng,
        end_lat,
        end_lng,
        nullif(btrim(member_casual), '') as member_casual,
        source_member,
        source_file_key,
        _ingested_at
    from source

),

classified as (

    select
        *,
        {{ ny_ts_status('started_at_local') }} as started_at_status,
        {{ ny_ts_status('ended_at_local') }} as ended_at_status
    from localized

),

resolved as (

    select
        *,
        case when started_at_status = 'valid' then {{ ny_ts_utc('started_at_local') }} end as started_at_utc,
        case when ended_at_status = 'valid' then {{ ny_ts_utc('ended_at_local') }} end as ended_at_utc
    from classified

)

select
    ride_id,
    rideable_type,
    started_at_raw,
    started_at_local,
    started_at_utc,
    started_at_status,
    ended_at_raw,
    ended_at_local,
    ended_at_utc,
    ended_at_status,
    case
        when started_at_status = 'valid' and ended_at_status = 'valid'
        then (extract(epoch from (ended_at_utc - started_at_utc)))::int
    end as trip_duration_sec,
    start_station_id,
    start_station_name,
    end_station_id,
    end_station_name,
    start_lat,
    start_lng,
    end_lat,
    end_lng,
    member_casual,
    source_member,
    source_file_key,
    _ingested_at
from resolved
