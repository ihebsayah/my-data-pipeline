with spine as (

    select generate_series(
        '{{ var('coverage_start') }}'::timestamptz,
        '{{ var('coverage_end') }}'::timestamptz - interval '1 hour',
        interval '1 hour'
    ) as hour_utc

),

observed as (

    select
        date_trunc('hour', started_at_utc at time zone 'UTC') at time zone 'UTC' as hour_utc,
        count(*) as observed_trips,
        min(started_at_utc) as first_trip_started_at
    from {{ ref('stg_trips') }}
    where started_at_status = 'valid'
        and started_at_utc >= '{{ var('coverage_start') }}'::timestamptz
        and started_at_utc < '{{ var('coverage_end') }}'::timestamptz
    group by 1

),

demand as (

    select
        hour_utc,
        rides_total,
        is_source_truncated
    from {{ ref('fct_hourly_demand') }}

),

joined as (

    select
        spine.hour_utc,
        coalesce(observed.observed_trips, 0)::int as observed_trips,
        observed.first_trip_started_at,
        demand.rides_total as demand_rides_total,
        demand.hour_utc is not null as in_demand_mart,
        coalesce(demand.is_source_truncated, false) as is_source_truncated,
        spine.hour_utc >= '{{ var('coverage_gap_start') }}'::timestamptz
            and spine.hour_utc < '{{ var('coverage_gap_end') }}'::timestamptz as in_outage_window,
        spine.hour_utc >= '{{ var('coverage_end') }}'::timestamptz - interval '1 day' as in_boundary_day,
        spine.hour_utc = date_trunc('hour', '{{ var('coverage_gap_end') }}'::timestamptz) as is_resume_hour
    from spine
    left join observed using (hour_utc)
    left join demand using (hour_utc)

),

classified as (

    select
        *,
        case
            when in_boundary_day then 'boundary'
            when in_outage_window and observed_trips > 0 then 'partial'
            when in_outage_window then 'unavailable'
            when is_resume_hour and (
                first_trip_started_at is null
                or first_trip_started_at > '{{ var('coverage_gap_end') }}'::timestamptz
            ) then 'partial'
            else 'complete'
        end as coverage_status
    from joined

)

select
    hour_utc,
    (hour_utc at time zone '{{ var("local_tz") }}') as hour_local_start,
    (hour_utc at time zone '{{ var("local_tz") }}')::date as hour_local_date,
    extract(hour from hour_utc at time zone '{{ var("local_tz") }}')::smallint as hour_local,
    extract(isodow from hour_utc at time zone '{{ var("local_tz") }}')::smallint as day_of_week_local,
    extract(dow from hour_utc at time zone '{{ var("local_tz") }}')::int in (0, 6) as is_weekend_local,
    coverage_status,
    in_demand_mart,
    observed_trips,
    demand_rides_total,
    is_source_truncated,
    case
        when coverage_status = 'boundary' then 'end_keyed_archive'
        when coverage_status = 'unavailable' then 'source_outage'
        when coverage_status = 'partial' and in_outage_window then 'gap_onset'
        when coverage_status = 'partial' then 'gap_resume'
    end as coverage_reason,
    first_trip_started_at
from classified
