{{ config(materialized='view') }}

select
    hour_local,
    is_weekend_local,
    count(*)::int as covered_hours,
    sum(rides_total)::int as rides_total,
    round(avg(rides_total), 1) as avg_rides_total,
    sum(rides_member)::int as rides_member,
    sum(rides_casual)::int as rides_casual,
    round(sum(rides_member)::numeric / nullif(sum(rides_total), 0), 4) as member_share
from {{ ref('fct_hourly_demand') }}
group by hour_local, is_weekend_local
