with ordered as (

    select
        month,
        coverage_start,
        coverage_end,
        lag(coverage_end) over (order by coverage_start) as previous_end
    from {{ ref('coverage_windows') }}

),

violations as (

    select
        month,
        'coverage_window_gap_or_overlap' as check_name,
        coverage_start::text as detail
    from ordered
    where previous_end is not null
        and coverage_start <> previous_end

)

select * from violations
