{% macro generate_schema_name(custom_schema_name, node) -%}
    {#- Schemas that must always resolve as bare names regardless of target.
        These are consumed by external pipelines and cannot be environment-prefixed. -#}
    {%- set fixed_schemas = ['epm', 'reference'] -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- elif custom_schema_name | trim in fixed_schemas -%}
        {{ custom_schema_name | trim }}
    {%- else -%}
        {{ target.schema }}_{{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
