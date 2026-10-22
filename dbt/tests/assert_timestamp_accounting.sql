with staged as (

    select * from {{ ref('stg_trips') }}

),

counts as (

    select
        count(*) as staged_rows,
        count(*) filter (where started_at_status = 'valid') as start_valid,
        count(*) filter (where started_at_status = 'ambiguous') as start_ambiguous,
        count(*) filter (where started_at_status = 'nonexistent') as start_nonexistent,
        count(*) filter (where started_at_status = 'invalid') as start_invalid,
        count(*) filter (where ended_at_status = 'valid') as end_valid,
        count(*) filter (where ended_at_status = 'ambiguous') as end_ambiguous,
        count(*) filter (where ended_at_status = 'nonexistent') as end_nonexistent,
        count(*) filter (where ended_at_status = 'invalid') as end_invalid,
        count(*) filter (where trip_duration_sec < 0) as negative_durations
    from staged

),

failed_checks as (

    select
        'started_at_status_partition' as check_name,
        (start_valid + start_ambiguous + start_nonexistent + start_invalid)::bigint as actual,
        staged_rows::bigint as expected
    from counts
    where (start_valid + start_ambiguous + start_nonexistent + start_invalid) <> staged_rows

    union all

    select
        'ended_at_status_partition',
        (end_valid + end_ambiguous + end_nonexistent + end_invalid)::bigint,
        staged_rows::bigint
    from counts
    where (end_valid + end_ambiguous + end_nonexistent + end_invalid) <> staged_rows

    union all

    select
        'no_negative_trip_duration',
        negative_durations::bigint,
        0::bigint
    from counts
    where negative_durations > 0

)

select * from failed_checks
