{{ config(materialized='view') }}

with coverage as (

    select * from {{ ref('fct_hourly_coverage') }}

),

demand as (

    select * from {{ ref('fct_hourly_demand') }}

)

select
    coverage.hour_local_date,
    extract(isodow from coverage.hour_local_date)::smallint as day_of_week_local,
    extract(dow from coverage.hour_local_date)::int in (0, 6) as is_weekend_local,
    count(*) filter (where coverage.in_demand_mart)::int as covered_hours,
    count(*) filter (where coverage.coverage_status = 'complete')::int as complete_hours,
    count(*) filter (where coverage.coverage_status = 'partial')::int as partial_hours,
    count(*) filter (where coverage.coverage_status = 'unavailable')::int as unavailable_hours,
    count(*) filter (where coverage.coverage_status = 'boundary')::int as boundary_hours,
    count(*) filter (where coverage.coverage_status = 'uncertain')::int as uncertain_hours,
    count(*)::int as expected_hours,
    bool_and(coverage.coverage_status = 'complete') as is_fully_covered_day,
    bool_or(coverage.is_source_truncated) as is_source_truncated_day,
    sum(coverage.observed_trips)::int as observed_trips,
    coalesce(sum(demand.rides_total), 0)::int as rides_total,
    coalesce(sum(demand.rides_member), 0)::int as rides_member,
    coalesce(sum(demand.rides_casual), 0)::int as rides_casual,
    (sum(coverage.observed_trips) - coalesce(sum(demand.rides_total), 0))::int as trips_excluded_from_demand
from coverage
left join demand using (hour_utc)
group by coverage.hour_local_date
