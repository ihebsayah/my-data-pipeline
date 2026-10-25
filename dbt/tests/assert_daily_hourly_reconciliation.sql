with daily as (

    select * from {{ ref('mart_daily_demand') }}

),

hourly as (

    select * from {{ ref('fct_hourly_demand') }}

),

hourly_rollup as (

    select
        hour_local_date,
        sum(rides_total)::int as rides_total,
        sum(rides_member)::int as rides_member,
        sum(rides_casual)::int as rides_casual,
        count(*)::int as covered_hours
    from hourly
    group by hour_local_date

),

per_day_mismatches as (

    select coalesce(daily.hour_local_date, hourly_rollup.hour_local_date) as hour_local_date
    from daily
    full outer join hourly_rollup using (hour_local_date)
    where daily.hour_local_date is null
        or hourly_rollup.hour_local_date is null
        or daily.rides_total <> hourly_rollup.rides_total
        or daily.rides_member <> hourly_rollup.rides_member
        or daily.rides_casual <> hourly_rollup.rides_casual
        or daily.covered_hours <> hourly_rollup.covered_hours

),

failed_checks as (

    select
        'daily_hourly_per_date_mismatch' as check_name,
        hour_local_date::text as detail
    from per_day_mismatches

    union all

    select
        'daily_hourly_grand_total',
        (select sum(rides_total) from daily)::text || ' vs ' || (select sum(rides_total) from hourly)::text
    where (select sum(rides_total) from daily) <> (select sum(rides_total) from hourly)

    union all

    select
        'daily_member_casual_sum',
        hour_local_date::text
    from daily
    where rides_member + rides_casual <> rides_total

    union all

    select
        'coverage_annotation_counts',
        hour_local_date::text
    from daily
    where complete_hours + partial_hours + unavailable_hours + boundary_hours
            + uncertain_hours <> daily.expected_hours
        or is_fully_covered_day <> (complete_hours = daily.expected_hours)
        or is_source_truncated_day <> (boundary_hours > 0)
        or trips_excluded_from_demand <> observed_trips - rides_total
        or observed_trips < rides_total

)

select * from failed_checks
