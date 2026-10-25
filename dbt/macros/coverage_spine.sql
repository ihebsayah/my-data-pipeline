{% macro coverage_spine() -%}
with windows as (

    select coverage_start, coverage_end, window_label
    from {{ ref('coverage_windows') }}

),

hours as (

    select
        generate_series(
            windows.coverage_start,
            windows.coverage_end - interval '1 hour',
            interval '1 hour'
        ) as hour_utc,
        windows.window_label,
        windows.coverage_end
    from windows

),

gaps as (

    select gap_start, gap_end
    from {{ ref('coverage_gaps') }}

)

select
    hours.hour_utc,
    hours.window_label,
    hours.coverage_end,
    exists (
        select 1
        from gaps
        where hours.hour_utc >= gaps.gap_start
          and hours.hour_utc < gaps.gap_end
    ) as in_outage_window,
    hours.hour_utc >= hours.coverage_end - interval '1 day' as in_boundary_day,
    exists (
        select 1
        from gaps
        where hours.hour_utc = date_trunc('hour', gaps.gap_end)
    ) as is_resume_hour,
    (
        select gaps.gap_end
        from gaps
        where hours.hour_utc = date_trunc('hour', gaps.gap_end)
        limit 1
    ) as resume_boundary,
    (
        select gaps.gap_start
        from gaps
        where hours.hour_utc >= gaps.gap_start
          and hours.hour_utc < gaps.gap_end
        limit 1
    ) as onset_gap_start,
    -- DST fall-back ambiguity: the local wall clock repeats, so two UTC hours
    -- render to the same local hour and neither can be attributed a verified
    -- trip count (source local timestamps there are ambiguous). Both UTC hours
    -- of the repeated local hour are flagged, classified 'uncertain' and
    -- excluded from the demand mart, never zeroed.
    (
        extract(epoch from (hours.hour_utc at time zone '{{ var("local_tz") }}'))
        - extract(epoch from (
            (hours.hour_utc - interval '1 hour') at time zone '{{ var("local_tz") }}'
        )) = 0
        or
        extract(epoch from (
            (hours.hour_utc + interval '1 hour') at time zone '{{ var("local_tz") }}'
        ))
        - extract(epoch from (hours.hour_utc at time zone '{{ var("local_tz") }}')) = 0
    ) as is_dst_ambiguous_hour
from hours
{%- endmacro %}
