with spine as (

    select hour_utc, window_label, coverage_end, in_outage_window,
           in_boundary_day, is_resume_hour, resume_boundary, onset_gap_start,
           is_dst_ambiguous_hour
    from ({{ coverage_spine() }}) spine

),

observed as (

    select
        date_trunc('hour', started_at_utc at time zone 'UTC') at time zone 'UTC' as hour_utc,
        count(*) as observed_trips,
        min(started_at_utc) as first_trip_started_at
    from {{ ref('stg_trips') }}
    where started_at_status = 'valid'
        and started_at_utc >= (select min(coverage_start) from {{ ref('coverage_windows') }})
        and started_at_utc < (select max(coverage_end) from {{ ref('coverage_windows') }})
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
        spine.coverage_end,
        spine.in_outage_window,
        spine.in_boundary_day,
        spine.is_resume_hour,
        spine.resume_boundary,
        spine.is_dst_ambiguous_hour,
        coalesce(observed.observed_trips, 0)::int as observed_trips,
        observed.first_trip_started_at,
        demand.rides_total as demand_rides_total,
        demand.hour_utc is not null as in_demand_mart,
        coalesce(demand.is_source_truncated, false) as is_source_truncated
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
            when is_dst_ambiguous_hour then 'uncertain'
            when is_resume_hour and (
                first_trip_started_at is null
                or first_trip_started_at > resume_boundary
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
        when coverage_status = 'uncertain' then 'dst_ambiguous_hour'
        when coverage_status = 'partial' and in_outage_window then 'gap_onset'
        when coverage_status = 'partial' then 'gap_resume'
    end as coverage_reason,
    first_trip_started_at
from classified
