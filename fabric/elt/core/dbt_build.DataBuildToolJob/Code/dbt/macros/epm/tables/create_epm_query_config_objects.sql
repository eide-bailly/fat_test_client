{# macro:
description: Orchestrator that executes all macros related to processing query parameters when preparing the source query.
#}
{% macro create_epm_query_config_objects() %}
    {%- if execute -%}
        {#- Create tables -#}
        {{ create_table__epm__callable_query() }}
        {{ create_table__epm__query_argument() }}
        {{ create_table__epm__watermark_state() }}

        {#- Create procedures -#}
        {{ create_procedure__epm__insert_query_argument() }}
        {{ create__procedure__epm__process_query_parameters() }}
        {{ create__procedure__epm__lookup_schedules() }}
        {{ create_procedure__lookup_extract_config() }}
    {%- endif -%}
{% endmacro %}
