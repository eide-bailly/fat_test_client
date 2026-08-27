-- timezone.sql — project-level timezone conversion macro
--
-- Wraps the dbt-date convert_timezone call with the project's local_timezone variable
-- so individual models do not need to hard-code a timezone string.
--
-- Usage in a model:
--   {{ convert_timezone('transaction_date') }}
--
-- The local_timezone variable is set in dbt_project.yml under vars: and defaults to
-- "America/Denver" (Mountain Time). Override it at the project or model level as needed.

{% macro convert_timezone(column) %}
    {{ dbt_date.convert_timezone(
        column=column,
        target_tz=var('local_timezone', 'America/Denver'),
        source_tz='UTC'
    ) }}
{% endmacro %}
