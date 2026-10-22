with spine as (

    select hour_utc
    from (
        select generate_series(
            '{{ var('coverage_start') }}'::timestamptz,
            '{{ var('coverage_gap_start') }}'::timestamptz - interval '1 hour',
            interval '1 hour'
        ) as hour_utc
        union all
        select generate_series(
            '{{ var('coverage_gap_end') }}'::timestamptz,
            '{{ var('coverage_end') }}'::timestamptz - interval '1 hour',
            interval '1 hour'
        ) as hour_utc
    ) coverage_hours

),

hourly_starts as (

    select
        date_trunc('hour', started_at_utc at time zone 'UTC') at time zone 'UTC' as hour_utc,
        count(*) as rides_total,
        count(*) filter (where member_casual = 'member') as rides_member,
        count(*) filter (where member_casual = 'casual') as rides_casual
    from {{ ref('stg_trips') }}
    where started_at_status = 'valid'
        and started_at_utc >= '{{ var('coverage_start') }}'::timestamptz
        and started_at_utc < '{{ var('coverage_end') }}'::timestamptz
        and not (
            started_at_utc >= '{{ var('coverage_gap_start') }}'::timestamptz
            and started_at_utc < '{{ var('coverage_gap_end') }}'::timestamptz
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
    spine.hour_utc >= '{{ var('coverage_end') }}'::timestamptz - interval '1 day' as is_source_truncated
from spine
left join hourly_starts using (hour_utc)
