with spine as (

    select hour_utc, window_label, coverage_end, in_outage_window, is_dst_ambiguous_hour
    from ({{ coverage_spine() }}) spine
    where not in_outage_window
      and not is_dst_ambiguous_hour

),

hourly_starts as (

    select
        date_trunc('hour', started_at_utc at time zone 'UTC') at time zone 'UTC' as hour_utc,
        count(*) as rides_total,
        count(*) filter (where member_casual = 'member') as rides_member,
        count(*) filter (where member_casual = 'casual') as rides_casual
    from {{ ref('stg_trips') }}
    where started_at_status = 'valid'
        and started_at_utc >= (select min(coverage_start) from {{ ref('coverage_windows') }})
        and started_at_utc < (select max(coverage_end) from {{ ref('coverage_windows') }})
        and not exists (
            select 1 from {{ ref('coverage_gaps') }} g
            where date_trunc('hour', started_at_utc at time zone 'UTC') at time zone 'UTC'
                    >= g.gap_start
              and date_trunc('hour', started_at_utc at time zone 'UTC') at time zone 'UTC'
                    < g.gap_end
        )
    group by 1

)

select
    spine.hour_utc,
    (spine.hour_utc at time zone '{{ var("local_tz") }}') as hour_local_start,
    (spine.hour_utc at time zone '{{ var("local_tz") }}')::date as hour_local_date,
    extract(hour from spine.hour_utc at time zone '{{ var("local_tz") }}')::smallint as hour_local,
    extract(isodow from spine.hour_utc at time zone '{{ var("local_tz") }}')::smallint as day_of_week_local,
    extract(dow from spine.hour_utc at time zone '{{ var("local_tz") }}')::int in (0, 6) as is_weekend_local,
    coalesce(hourly_starts.rides_total, 0)::int as rides_total,
    coalesce(hourly_starts.rides_member, 0)::int as rides_member,
    coalesce(hourly_starts.rides_casual, 0)::int as rides_casual,
    spine.hour_utc >= spine.coverage_end - interval '1 day' as is_source_truncated
from spine
left join hourly_starts using (hour_utc)
