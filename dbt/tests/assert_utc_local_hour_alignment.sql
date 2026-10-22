select
    hour_utc,
    hour_local_start,
    hour_local_date,
    hour_local,
    day_of_week_local,
    is_weekend_local
from {{ ref('fct_hourly_demand') }}
where extract(minute from hour_utc) <> 0
    or extract(second from hour_utc) <> 0
    or hour_local_start <> (hour_utc at time zone '{{ var("local_tz") }}')
    or hour_local_date <> (hour_utc at time zone '{{ var("local_tz") }}')::date
    or hour_local <> extract(hour from hour_utc at time zone '{{ var("local_tz") }}')::int
    or day_of_week_local <> extract(isodow from hour_utc at time zone '{{ var("local_tz") }}')::int
    or is_weekend_local <> (extract(dow from hour_utc at time zone '{{ var("local_tz") }}')::int in (0, 6))
