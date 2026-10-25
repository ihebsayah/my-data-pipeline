with eligible as (

    select
        start_station_id,
        start_station_name,
        member_casual,
        started_at_utc
    from {{ ref('stg_trips') }}
    where started_at_status = 'valid'
        and started_at_utc >= (select min(coverage_start) from {{ ref('coverage_windows') }})
        and started_at_utc < (select max(coverage_end) from {{ ref('coverage_windows') }})
        and not exists (
            select 1 from {{ ref('coverage_gaps') }} g
            where started_at_utc >= g.gap_start
              and started_at_utc < g.gap_end
        )

),

-- Station id is the dimension key; name is an attribute. A station id can
-- carry more than one published name across archives (source-side rename /
-- typo; observed for 4077.07 across Oct 2025 - Jan 2026). Resolve the
-- canonical name deterministically as the most-frequent name per id, breaking
-- ties by name, so id -> name is 1:1 while every ride is still counted.
canonical_name as (

    select
        start_station_id,
        start_station_name,
        count(*) as name_rides
    from eligible
    where start_station_id is not null
    group by start_station_id, start_station_name

),

resolved as (

    select start_station_id, start_station_name
    from (
        select
            start_station_id,
            start_station_name,
            row_number() over (
                partition by start_station_id
                order by name_rides desc, start_station_name
            ) as rn
        from canonical_name
    ) ranked
    where rn = 1

),

counted as (

    select
        start_station_id,
        count(*)::int as rides_total,
        count(*) filter (where member_casual = 'member')::int as rides_member,
        count(*) filter (where member_casual = 'casual')::int as rides_casual,
        min(started_at_utc) as first_trip_started_at,
        max(started_at_utc) as last_trip_started_at
    from eligible
    group by start_station_id

)

select
    counted.start_station_id,
    resolved.start_station_name,
    counted.rides_total,
    counted.rides_member,
    counted.rides_casual,
    round(counted.rides_total::numeric / sum(counted.rides_total) over (), 6) as share_of_all_rides,
    counted.first_trip_started_at,
    counted.last_trip_started_at
from counted
left join resolved using (start_station_id)
