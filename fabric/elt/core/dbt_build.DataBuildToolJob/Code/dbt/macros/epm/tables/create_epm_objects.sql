{# macro:
description: Executes all macros related to the EPM framework.
#}
{% macro create_epm_objects() %}
    {%- set create_schema_sql -%}
        use {{ generate_epm_database() }};

        if (schema_id('{{ generate_schema_name("epm") }}') is null)
        begin
            exec('create schema {{ generate_schema_name("epm") }}');
        end
    {%- endset -%}

    {%- if execute -%}
        {%- do run_query(create_schema_sql) -%}
        {{ print('Created schema ' ~ generate_epm_database_schema() ~ ' if it did not already exist.') }}

        {{ create_epm_query_config_objects() }}
    {%- endif -%}
{% endmacro %}

{# macro:
description: Returns the environment-specific database for the EPM framework.
#}
{% macro generate_epm_database() %}
    {{ return(generate_database_name(var('metadata_database_name'))) }}
{%- endmacro %}

{# macro:
description: Returns the environment-specific database and schema name for the EPM framework.
#}
{% macro generate_epm_database_schema() %}
    {{ return(generate_epm_database() ~ '.' ~ generate_schema_name('epm')) }}
{%- endmacro %}

{# macro:
description: Returns the environment specific constraint name for the EPM framework.
arguments:
  - name: table_name
    description: The name of the table.
  - name: column_name
    description: Optional. The name of the column. If not provided, the constraint name will not include the column name.
  - name: constraint_type
    description: Optional. The type of constraint. Possible values are 'primary_key', 'foreign_key', and 'unique'.
#}
{% macro generate_epm_contraint_name(table_name, column_name=none, constraint_type='primary_key') %}
    {{ generate_constraint_name(
        table_schema=generate_schema_name('epm')
        ,table_name=table_name
        ,column_name=column_name
        ,constraint_type=constraint_type
    ) }}
{%- endmacro %}

{# macro:
description: Returns the name needed to create a constraint with the table schema, table name, and column name separated by double underscores.
arguments:
  - name: table_schema
    description: The schema name of the table.
  - name: table_name
    description: The name of the table.
  - name: column_name
    description: Optional. The name of the column. If not provided, the constraint name will not include the column name.
  - name: constraint_type
    description: Optional. The type of constraint. Possible values are 'primary_key', 'foreign_key', and 'unique'.
#}
{% macro generate_constraint_name(table_schema, table_name, column_name=none, constraint_type='primary_key') %}
    {%- if constraint_type == 'primary_key' -%}
        {%- set constraint_name_prefix = 'pk' -%}
    {%- elif constraint_type == 'foreign_key' -%}
        {%- set constraint_name_prefix = 'fk' -%}
    {%- elif constraint_type == 'unique' -%}
        {%- set constraint_name_prefix = 'uk' -%}
    {%- else -%}
        {%- set constraint_name_prefix = constraint_type -%}
    {%- endif -%}

    {%- set constraint_name = table_schema ~ '__' ~ table_name -%}

    {%- if column_name is not none -%}
        {%- set constraint_name = constraint_name ~ '__' ~ column_name -%}
    {%- endif -%}

    {%- set constraint_name = constraint_name_prefix ~ '__' ~ constraint_name -%}

    {{ return(constraint_name) }}
{%- endmacro %}
