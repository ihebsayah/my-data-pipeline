with station as (

    select * from {{ ref('mart_station_starts') }}

),

eligible as (

    select
        start_station_id,
        start_station_name,
        member_casual
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

eligible_counts as (

    select
        count(*) as eligible_total,
        count(*) filter (where start_station_id is null) as eligible_null_station
    from eligible

),

station_counts as (

    select
        coalesce(sum(rides_total), 0) as station_total,
        coalesce(sum(rides_total) filter (where start_station_id is null), 0) as station_null_total
    from station

),

failed_checks as (

    select
        'station_sum_vs_eligible' as check_name,
        station_counts.station_total::text || ' vs ' || eligible_counts.eligible_total::text as detail
    from eligible_counts, station_counts
    where station_counts.station_total <> eligible_counts.eligible_total

    union all

    select
        'station_sum_vs_demand_mart',
        (select sum(rides_total) from station)::text || ' vs '
            || (select sum(rides_total) from {{ ref('fct_hourly_demand') }})::text
    where (select sum(rides_total) from station)
        <> (select sum(rides_total) from {{ ref('fct_hourly_demand') }})

    union all

    select
        'station_member_casual_sum',
        start_station_id || ' / ' || coalesce(start_station_name, '<null>')
    from station
    where rides_member + rides_casual <> rides_total

    union all

    select
        'duplicate_station_pair',
        start_station_id || ' / ' || coalesce(start_station_name, '<null>')
    from station
    group by start_station_id, start_station_name
    having count(*) > 1

    union all

    select
        'station_id_to_name_not_unique',
        start_station_id
    from station
    where start_station_id is not null
    group by start_station_id
    having count(distinct start_station_name) > 1

    union all

    select
        'null_station_group_count',
        (select count(*) from station where start_station_id is null)::text
            || ' null group(s), '
            || (select count(*) from eligible where start_station_id is null)::text
            || ' eligible null-station trips'
    where (select count(*) from station where start_station_id is null) <> 1
        or (select sum(rides_total) from station where start_station_id is null)
            <> (select count(*) from eligible where start_station_id is null)

    union all

    select
        'null_station_trips_dropped',
        (select count(*) from eligible where start_station_id is null)::text
            || ' eligible vs ' || coalesce((
                select sum(rides_total) from station where start_station_id is null
            ), 0)::text || ' in station model'
    where coalesce((
            select sum(rides_total) from station where start_station_id is null
        ), 0) <> (select count(*) from eligible where start_station_id is null)

    union all

    select
        'non_null_station_trips_dropped',
        (select count(*) from eligible where start_station_id is not null)::text
            || ' eligible vs ' || coalesce((
                select sum(rides_total) from station where start_station_id is not null
            ), 0)::text || ' in station model'
    where coalesce((
            select sum(rides_total) from station where start_station_id is not null
        ), 0) <> (select count(*) from eligible where start_station_id is not null)

)

select * from failed_checks
