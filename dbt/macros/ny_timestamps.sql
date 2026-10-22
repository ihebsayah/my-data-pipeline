{% macro ny_local_ts(raw_sql) -%}
case
    when {{ raw_sql }} is null then null
    when {{ raw_sql }} !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?$' then null
    when substr({{ raw_sql }}, 6, 2)::int < 1 or substr({{ raw_sql }}, 6, 2)::int > 12 then null
    when substr({{ raw_sql }}, 9, 2)::int < 1 then null
    when substr({{ raw_sql }}, 9, 2)::int > (
        case
            when substr({{ raw_sql }}, 6, 2)::int in (1, 3, 5, 7, 8, 10, 12) then 31
            when substr({{ raw_sql }}, 6, 2)::int in (4, 6, 9, 11) then 30
            when substr({{ raw_sql }}, 1, 4)::int % 4 = 0
                and (substr({{ raw_sql }}, 1, 4)::int % 100 <> 0
                     or substr({{ raw_sql }}, 1, 4)::int % 400 = 0) then 29
            else 28
        end
    ) then null
    when substr({{ raw_sql }}, 12, 2)::int > 23 then null
    when substr({{ raw_sql }}, 15, 2)::int > 59 then null
    when substr({{ raw_sql }}, 18, 2)::int > 59 then null
    else {{ raw_sql }}::timestamp
end
{%- endmacro %}

{% macro ny_ts_status(local_sql) -%}
case
    when {{ local_sql }} is null then 'invalid'
    when ({{ local_sql }} at time zone '{{ var("local_tz") }}') at time zone '{{ var("local_tz") }}'
         <> {{ local_sql }} then 'nonexistent'
    when ((
        ((({{ local_sql }} at time zone '{{ var("local_tz") }}') - interval '1 hour')
            at time zone '{{ var("local_tz") }}') = {{ local_sql }}
        or
        ((({{ local_sql }} at time zone '{{ var("local_tz") }}') + interval '1 hour')
            at time zone '{{ var("local_tz") }}') = {{ local_sql }}
    )) then 'ambiguous'
    else 'valid'
end
{%- endmacro %}

{% macro ny_ts_utc(local_sql) -%}
({{ local_sql }} at time zone '{{ var("local_tz") }}')
{%- endmacro %}
