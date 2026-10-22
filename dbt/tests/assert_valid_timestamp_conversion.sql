with staged as (

    select * from {{ ref('stg_trips') }}

),

checks as (

    select
        count(*) filter (where started_at_status = 'invalid' or ended_at_status = 'invalid') as invalid_timestamps,
        count(*) filter (
            where started_at_status = 'valid'
            and (started_at_utc at time zone '{{ var("local_tz") }}') <> started_at_local
        ) as started_roundtrip_failures,
        count(*) filter (
            where ended_at_status = 'valid'
            and (ended_at_utc at time zone '{{ var("local_tz") }}') <> ended_at_local
        ) as ended_roundtrip_failures,
        count(*) filter (where started_at_status = 'valid' and started_at_utc is null) as valid_start_missing_utc,
        count(*) filter (where ended_at_status = 'valid' and ended_at_utc is null) as valid_end_missing_utc,
        count(*) filter (where started_at_status <> 'valid' and started_at_utc is not null) as unresolved_start_with_utc,
        count(*) filter (where ended_at_status <> 'valid' and ended_at_utc is not null) as unresolved_end_with_utc
    from staged

)

select *
from checks
where invalid_timestamps > 0
    or started_roundtrip_failures > 0
    or ended_roundtrip_failures > 0
    or valid_start_missing_utc > 0
    or valid_end_missing_utc > 0
    or unresolved_start_with_utc > 0
    or unresolved_end_with_utc > 0
