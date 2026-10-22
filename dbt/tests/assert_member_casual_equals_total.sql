select *
from {{ ref('fct_hourly_demand') }}
where rides_member + rides_casual <> rides_total
