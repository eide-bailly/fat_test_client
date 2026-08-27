-- Fabric-specific implementation of dbt_date.convert_timezone().
-- Overrides the upstream dbt-date macro to use T-SQL AT TIME ZONE syntax,
-- which is the correct approach for Microsoft Fabric Warehouse (Synapse SQL engine).
--
-- Source reference: dbt_date/macros/calendar_date/convert_timezone.sql
-- Dispatch is wired in dbt_project.yml: macro_namespace dbt_date → search_order ['fat_test_client', 'dbt_date']
--
-- Parameters:
--   column    (string) — the column expression to convert
--   target_tz (string) — IANA timezone string to convert to (e.g., "America/Denver")
--   source_tz (string) — IANA timezone string to convert from (default: "UTC")

{% macro fabric__convert_timezone(column, target_tz, source_tz='UTC') -%}
    cast(
        cast({{ column }} as {{ dbt.type_timestamp() }})
            at time zone '{{ source_tz }}'
            at time zone '{{ target_tz }}'
        as datetime2(6)
    )
{%- endmacro %}
