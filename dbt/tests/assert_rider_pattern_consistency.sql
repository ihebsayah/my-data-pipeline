with pattern as (

    select * from {{ ref('mart_demand_pattern_hourly') }}

),

mix as (

    select * from {{ ref('mart_rider_mix') }}

),

demand as (

    select * from {{ ref('fct_hourly_demand') }}

),

failed_checks as (

    select
        'pattern_total_vs_demand' as check_name,
        (select sum(rides_total) from pattern)::text || ' vs '
            || (select sum(rides_total) from demand)::text as detail
    where (select sum(rides_total) from pattern) <> (select sum(rides_total) from demand)

    union all

    select
        'pattern_member_vs_demand',
        (select sum(rides_member) from pattern)::text || ' vs '
            || (select sum(rides_member) from demand)::text
    where (select sum(rides_member) from pattern) <> (select sum(rides_member) from demand)

    union all

    select
        'pattern_casual_vs_demand',
        (select sum(rides_casual) from pattern)::text || ' vs '
            || (select sum(rides_casual) from demand)::text
    where (select sum(rides_casual) from pattern) <> (select sum(rides_casual) from demand)

    union all

    select
        'pattern_member_casual_sum',
        hour_local || ' / weekend=' || is_weekend_local::text
    from pattern
    where rides_member + rides_casual <> rides_total

    union all

    select
        'pattern_expected_row_count',
        (select count(*) from pattern)::text || ' rows (expected 48)'
    where (select count(*) from pattern) <> 48

    union all

    select
        'pattern_covered_hours_vs_demand',
        (select sum(covered_hours) from pattern)::text || ' vs '
            || (select count(*) from demand)::text
    where (select sum(covered_hours) from pattern) <> (select count(*) from demand)

    union all

    select
        'mix_total_vs_demand',
        (select sum(rides_total) from mix)::text || ' vs '
            || (select sum(rides_total) from demand)::text
    where (select sum(rides_total) from mix) <> (select sum(rides_total) from demand)

    union all

    select
        'mix_member_casual_sum',
        'weekend=' || is_weekend_local::text
    from mix
    where rides_member + rides_casual <> rides_total

    union all

    select
        'mix_share_sum',
        'weekend=' || is_weekend_local::text
    from mix
    where round(member_share + casual_share, 4) <> 1.0000

    union all

    select
        'mix_expected_row_count',
        (select count(*) from mix)::text || ' rows (expected 2)'
    where (select count(*) from mix) <> 2

    union all

    select
        'mix_covered_hours_vs_demand',
        (select sum(covered_hours) from mix)::text || ' vs '
            || (select count(*) from demand)::text
    where (select sum(covered_hours) from mix) <> (select count(*) from demand)

)

select * from failed_checks
