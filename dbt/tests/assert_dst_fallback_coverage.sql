with coverage as (

    select * from {{ ref('fct_hourly_coverage') }}

),

demand as (

    select * from {{ ref('fct_hourly_demand') }}

),

-- The 2025-11-02 America/New_York fall-back: local wall clock 01:00 occurs
-- twice (EDT then EST), i.e. UTC 05:00 and 06:00 both render to 01:00 local.
-- Source local timestamps in that repeated hour are ambiguous and cannot be
-- placed in a UTC bucket, so both UTC hours must be 'uncertain', absent from
-- the demand mart, and never rendered as zero demand.
fallback_day as (

    select
        coverage.hour_utc,
        coverage.hour_local_start,
        coverage.coverage_status,
        coverage.coverage_reason,
        coverage.demand_rides_total,
        coverage.in_demand_mart,
        demand.hour_utc is not null as demand_row
    from coverage
    left join demand using (hour_utc)
    where coverage.hour_local_date = date '2025-11-02'

),

repeated_local_hour as (

    select
        hour_local_start,
        count(*) as utc_hours
    from fallback_day
    group by hour_local_start
    having count(*) > 1

),

failed_checks as (

    select
        'repeated_local_hour_count' as check_name,
        coalesce(max(utc_hours), 0)::text || ' repeated hour(s): '
            || count(*)::text as detail
    from repeated_local_hour
    having count(*) <> 1 or max(utc_hours) <> 2

    union all

    select
        'ambiguous_hour_status',
        hour_utc::text
    from fallback_day
    where hour_local_start::time = time '01:00'
        and (coverage_status <> 'uncertain' or coverage_reason <> 'dst_ambiguous_hour')

    union all

    select
        'ambiguous_hour_in_demand_mart',
        hour_utc::text
    from fallback_day
    where hour_local_start::time = time '01:00'
        and (in_demand_mart or demand_row or demand_rides_total is not null)

    union all

    select
        'fallback_day_spine_hours',
        n::text
    from (select count(*) as n from fallback_day) x
    where x.n <> 25

)

select * from failed_checks
