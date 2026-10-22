with fixture as (

    select *
    from (values
        ('normal_winter', '2026-01-15 12:34:56.789', 'valid'),
        ('normal_summer', '2026-07-15 12:34:56.789', 'valid'),
        ('day_before_fall_back', '2025-11-01 23:59:59.999', 'valid'),
        ('ambiguous_fall_back_mid', '2025-11-02 01:30:00.000', 'ambiguous'),
        ('ambiguous_fall_back_last_sec', '2025-11-02 01:59:59.999', 'ambiguous'),
        ('valid_after_fall_back', '2025-11-02 02:00:00.000', 'valid'),
        ('valid_before_spring_forward', '2026-03-08 01:59:59.999', 'valid'),
        ('nonexistent_spring_forward', '2026-03-08 02:30:00.000', 'nonexistent'),
        ('valid_after_spring_forward', '2026-03-08 03:00:00.000', 'valid'),
        ('invalid_calendar_day', '2026-02-30 10:00:00.000', 'invalid'),
        ('invalid_month', '2026-13-01 10:00:00.000', 'invalid'),
        ('invalid_zero_month', '2026-00-10 10:00:00.000', 'invalid'),
        ('invalid_zero_day', '2026-01-00 10:00:00.000', 'invalid'),
        ('invalid_hour', '2026-01-15 25:00:00.000', 'invalid'),
        ('invalid_minute', '2026-01-15 12:60:00.000', 'invalid'),
        ('invalid_leap_second', '2026-01-15 12:00:60.000', 'invalid'),
        ('invalid_format', 'not-a-timestamp', 'invalid'),
        ('invalid_short_format', '2026-01-15 12:00', 'invalid'),
        ('null_input', null, 'invalid')
    ) as t(case_name, raw_ts, expected_status)

),

classified as (

    select
        case_name,
        raw_ts,
        expected_status,
        {{ ny_local_ts('raw_ts') }} as local_ts,
        {{ ny_ts_status(ny_local_ts('raw_ts')) }} as actual_status
    from fixture

),

with_utc as (

    select
        *,
        case when actual_status = 'valid' then {{ ny_ts_utc('local_ts') }} end as actual_utc
    from classified

)

select
    case_name,
    raw_ts,
    expected_status,
    actual_status,
    actual_utc
from with_utc
where actual_status <> expected_status
    or (expected_status = 'valid') <> (actual_utc is not null)
    or (actual_status = 'valid'
        and (actual_utc at time zone '{{ var("local_tz") }}') <> local_ts)
