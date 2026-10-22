with coverage as (

    select * from {{ ref('fct_hourly_coverage') }}

),

failed_checks as (

    select
        'in_demand_mart_status_mismatch' as check_name,
        hour_utc::text as detail
    from coverage
    where in_demand_mart <> (
        coverage_status in ('complete', 'boundary') or coverage_reason = 'gap_resume'
    )

    union all

    select
        'demand_value_null_mismatch',
        hour_utc::text
    from coverage
    where (demand_rides_total is null) <> (not in_demand_mart)

    union all

    select
        'complete_status_with_reason',
        hour_utc::text
    from coverage
    where coverage_status = 'complete' and coverage_reason is not null

    union all

    select
        'unavailable_status_with_trips_or_demand',
        hour_utc::text
    from coverage
    where coverage_status = 'unavailable'
        and (observed_trips <> 0 or demand_rides_total is not null or in_demand_mart)

    union all

    select
        'partial_status_without_observed_trips',
        hour_utc::text
    from coverage
    where coverage_status = 'partial' and observed_trips <= 0

    union all

    select
        'boundary_status_mismatch',
        hour_utc::text
    from coverage
    where (coverage_status = 'boundary') <> is_source_truncated

    union all

    select
        'boundary_status_wrong_reason',
        hour_utc::text
    from coverage
    where coverage_status = 'boundary'
        and (coverage_reason <> 'end_keyed_archive' or not in_demand_mart)

    union all

    select
        'outage_reason_without_unavailable_or_onset',
        hour_utc::text
    from coverage
    where coverage_reason = 'source_outage'
        and coverage_status <> 'unavailable'

    union all

    select
        'resume_hour_without_late_first_trip',
        hour_utc::text
    from coverage
    where coverage_reason = 'gap_resume'
        and (first_trip_started_at is null or first_trip_started_at <= hour_utc)

    union all

    select
        'gap_resume_hours_in_wrong_bucket',
        hour_utc::text
    from coverage
    where coverage_reason = 'gap_resume'
        and (not in_demand_mart or demand_rides_total is null
             or hour_utc < '{{ var('coverage_gap_end') }}'::timestamptz
             or coverage_status <> 'partial')

    union all

    select
        'gap_onset_hours_in_wrong_bucket',
        hour_utc::text
    from coverage
    where coverage_reason = 'gap_onset'
        and (in_demand_mart or demand_rides_total is not null
             or not (hour_utc >= '{{ var('coverage_gap_start') }}'::timestamptz
                     and hour_utc < '{{ var('coverage_gap_end') }}'::timestamptz)
             or coverage_status <> 'partial')

    union all

    select
        'complete_hour_missing_trips_field',
        hour_utc::text
    from coverage
    where coverage_status in ('complete', 'boundary') and demand_rides_total is null

    union all

    select
        'window_row_count',
        'expected 744, got ' || count(*)::text
    from coverage
    group by ()
    having count(*) <> 744

)

select * from failed_checks
