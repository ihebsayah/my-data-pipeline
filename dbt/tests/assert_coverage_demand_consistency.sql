with coverage as (

    select * from {{ ref('fct_hourly_coverage') }}

),

demand as (

    select * from {{ ref('fct_hourly_demand') }}

),

failed_checks as (

    select
        'coverage_row_missing_for_demand_hour' as check_name,
        demand.hour_utc::text as detail
    from demand
    left join coverage using (hour_utc)
    where coverage.hour_utc is null

    union all

    select
        'flagged_in_mart_without_demand_row',
        coverage.hour_utc::text
    from coverage
    left join demand using (hour_utc)
    where coverage.in_demand_mart and demand.hour_utc is null

    union all

    select
        'demand_row_without_in_mart_flag',
        coverage.hour_utc::text
    from coverage
    join demand using (hour_utc)
    where not coverage.in_demand_mart

    union all

    select
        'demand_value_mismatch',
        coverage.hour_utc::text
    from coverage
    join demand using (hour_utc)
    where coverage.demand_rides_total <> demand.rides_total

    union all

    select
        'coverage_demand_sum_vs_demand_mart',
        coalesce((select sum(demand_rides_total) from coverage), 0)::text
            || ' vs ' || (select sum(rides_total) from demand)::text
    where coalesce((select sum(demand_rides_total) from coverage), 0)
        <> (select sum(rides_total) from demand)

    union all

    select
        'observed_trips_below_demand',
        coverage.hour_utc::text
    from coverage
    join demand using (hour_utc)
    where coverage.observed_trips < demand.rides_total

)

select * from failed_checks
