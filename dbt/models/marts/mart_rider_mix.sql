{{ config(materialized='view') }}

select
    is_weekend_local,
    count(*)::int as covered_hours,
    sum(rides_total)::int as rides_total,
    sum(rides_member)::int as rides_member,
    sum(rides_casual)::int as rides_casual,
    round(sum(rides_member)::numeric / nullif(sum(rides_total), 0), 4) as member_share,
    round(sum(rides_casual)::numeric / nullif(sum(rides_total), 0), 4) as casual_share
from {{ ref('fct_hourly_demand') }}
group by is_weekend_local
