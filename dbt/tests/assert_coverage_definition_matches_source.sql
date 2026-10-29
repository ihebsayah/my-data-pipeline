with spine as (

    select hour_utc, in_outage_window
    from ({{ coverage_spine() }}) spine
    where not in_outage_window
      and not is_dst_ambiguous_hour

),

observed as (

    select distinct date_trunc('hour', started_at_utc at time zone 'UTC') at time zone 'UTC' as hour_utc
    from {{ ref('stg_trips') }}
    where started_at_status = 'valid'
        and started_at_utc >= (select min(coverage_start) from {{ ref('coverage_windows') }})
        and started_at_utc < (select max(coverage_end) from {{ ref('coverage_windows') }})

),

violations as (

    select
        spine.hour_utc,
        'covered_hour_without_observed_trips' as violation
    from spine
    left join observed using (hour_utc)
    left join {{ ref('fct_hourly_coverage') }} coverage using (hour_utc)
    where observed.hour_utc is null
        and (coverage.coverage_status is distinct from 'uncertain'
             or coverage.coverage_reason is distinct from 'unexplained_source_gap'
             or coverage.in_demand_mart
             or coverage.demand_rides_total is not null)

    union all

    select
        observed.hour_utc,
        'observed_hour_outside_coverage_spines' as violation
    from observed
    left join spine using (hour_utc)
    where spine.hour_utc is null
        and not exists (
            select 1 from {{ ref('coverage_gaps') }} g
            where observed.hour_utc >= g.gap_start
              and observed.hour_utc < g.gap_end
        )

)

select * from violations
