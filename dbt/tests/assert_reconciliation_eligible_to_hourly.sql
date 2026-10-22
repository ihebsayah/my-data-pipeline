with staged as (

    select * from {{ ref('stg_trips') }}

),

spine as (

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

counts as (

    select
        (select count(*) from {{ source('raw', 'trips') }}) as raw_rows,
        count(*) as staged_rows,
        count(*) filter (where started_at_status <> 'valid') as unresolved_starts,
        count(*) filter (where started_at_status = 'valid') as valid_starts,
        count(*) filter (
            where started_at_status = 'valid'
            and started_at_utc < '{{ var('coverage_start') }}'::timestamptz
        ) as boundary_before_window,
        count(*) filter (
            where started_at_status = 'valid'
            and started_at_utc >= '{{ var('coverage_end') }}'::timestamptz
        ) as boundary_after_window,
        count(*) filter (
            where started_at_status = 'valid'
            and started_at_utc >= '{{ var('coverage_gap_start') }}'::timestamptz
            and started_at_utc < '{{ var('coverage_gap_end') }}'::timestamptz
        ) as starts_in_outage_window
    from staged

),

recomputed as (

    select
        date_trunc('hour', started_at_utc at time zone 'UTC') at time zone 'UTC' as hour_utc,
        count(*) as rides_total,
        count(*) filter (where member_casual = 'member') as rides_member,
        count(*) filter (where member_casual = 'casual') as rides_casual
    from staged
    where started_at_status = 'valid'
        and started_at_utc >= '{{ var('coverage_start') }}'::timestamptz
        and started_at_utc < '{{ var('coverage_end') }}'::timestamptz
        and not (
            started_at_utc >= '{{ var('coverage_gap_start') }}'::timestamptz
            and started_at_utc < '{{ var('coverage_gap_end') }}'::timestamptz
        )
    group by 1

),

per_hour_mismatches as (

    select coalesce(recomputed.hour_utc, mart.hour_utc) as hour_utc
    from recomputed
    full outer join {{ ref('fct_hourly_demand') }} mart using (hour_utc)
    where coalesce(recomputed.rides_total, 0) <> coalesce(mart.rides_total, 0)
        or coalesce(recomputed.rides_member, 0) <> coalesce(mart.rides_member, 0)
        or coalesce(recomputed.rides_casual, 0) <> coalesce(mart.rides_casual, 0)

),

failed_checks as (

    select
        'staged_rows_eq_raw_rows' as check_name,
        staged_rows::bigint as actual,
        raw_rows::bigint as expected
    from counts
    where staged_rows <> raw_rows

    union all

    select
        'valid_starts_partition',
        (boundary_before_window + boundary_after_window + starts_in_outage_window
            + (select sum(rides_total) from {{ ref('fct_hourly_demand') }}))::bigint,
        valid_starts::bigint
    from counts
    where (boundary_before_window + boundary_after_window + starts_in_outage_window
            + (select sum(rides_total) from {{ ref('fct_hourly_demand') }})
          ) <> valid_starts

    union all

    select
        'mart_rows_eq_spine_hours',
        (select count(*) from {{ ref('fct_hourly_demand') }})::bigint,
        (select count(*) from spine)::bigint
    where (select count(*) from {{ ref('fct_hourly_demand') }}) <> (select count(*) from spine)

    union all

    select
        'per_hour_counts_match_staging',
        (select count(*) from per_hour_mismatches)::bigint,
        0::bigint
    where (select count(*) from per_hour_mismatches) > 0

)

select * from failed_checks
