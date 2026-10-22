with eligible as (

    select
        start_station_id,
        start_station_name,
        member_casual,
        started_at_utc
    from {{ ref('stg_trips') }}
    where started_at_status = 'valid'
        and started_at_utc >= '{{ var('coverage_start') }}'::timestamptz
        and started_at_utc < '{{ var('coverage_end') }}'::timestamptz
        and not (
            started_at_utc >= '{{ var('coverage_gap_start') }}'::timestamptz
            and started_at_utc < '{{ var('coverage_gap_end') }}'::timestamptz
        )

)

select
    start_station_id,
    start_station_name,
    count(*)::int as rides_total,
    count(*) filter (where member_casual = 'member')::int as rides_member,
    count(*) filter (where member_casual = 'casual')::int as rides_casual,
    round(count(*)::numeric / sum(count(*)) over (), 6) as share_of_all_rides,
    min(started_at_utc) as first_trip_started_at,
    max(started_at_utc) as last_trip_started_at
from eligible
group by start_station_id, start_station_name
