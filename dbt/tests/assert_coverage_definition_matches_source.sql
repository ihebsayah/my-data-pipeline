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

observed as (

    select distinct date_trunc('hour', started_at_utc at time zone 'UTC') at time zone 'UTC' as hour_utc
    from {{ ref('stg_trips') }}
    where started_at_status = 'valid'

),

violations as (

    select
        spine.hour_utc,
        'covered_hour_without_observed_trips' as violation
    from spine
    left join observed using (hour_utc)
    where observed.hour_utc is null

    union all

    select
        observed.hour_utc,
        'observed_hour_outside_coverage_spines' as violation
    from observed
    left join spine using (hour_utc)
    where spine.hour_utc is null
        and observed.hour_utc <> '{{ var('coverage_gap_start') }}'::timestamptz
        and observed.hour_utc >= '{{ var('coverage_start') }}'::timestamptz

)

select * from violations
