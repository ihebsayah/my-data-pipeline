-- The New York spring-forward day has 23 actual hours and no local 02:00.
with spring as (
    select * from {{ ref('fct_hourly_coverage') }}
    where hour_utc >= timestamptz '2025-03-09 05:00:00+00'
      and hour_utc < timestamptz '2025-03-10 04:00:00+00'
), failures as (
    select 'spring_day_hour_count' as violation
    where exists (select 1 from {{ ref('coverage_windows') }} where month = '202503')
      and (select count(*) from spring) <> 23
    union all
    select 'invented_spring_local_hour' from spring where hour_local = 2
    union all
    select 'unresolved_start_assigned_utc'
    from {{ ref('stg_trips') }}
    where started_at_status in ('ambiguous', 'nonexistent', 'invalid')
      and started_at_utc is not null
)
select * from failures
