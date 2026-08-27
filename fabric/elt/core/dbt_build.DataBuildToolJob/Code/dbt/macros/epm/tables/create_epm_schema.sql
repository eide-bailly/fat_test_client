--@ Creates the EPM schema in the Fabric Warehouse if it does not already exist.
--@ Call this from an on-run-start hook in dbt_project.yml, or run it once
--@ during environment setup via: dbt run-operation create_epm_schema
--@
--@ This macro creates ONLY the schema. It does NOT create the watermark_state
--@ table — that table is managed by the dbt incremental model of the same name.
--@ Creating the table here would conflict with dbt's first-run CREATE TABLE.
--@ It also does NOT create run-logging or audit tables (covered by Workspace Monitoring).
{% macro create_epm_schema() %}
    {%- set schema_name = generate_schema_name('epm') -%}
    {%- set database_name = generate_database_name(var('metadata_database_name', target.database)) -%}

    {%- set create_schema_sql -%}
        USE {{ database_name }};

        IF (SCHEMA_ID('{{ schema_name }}') IS NULL)
        BEGIN
            EXEC('CREATE SCHEMA {{ schema_name }}');
        END
    {%- endset -%}

    {%- if execute -%}
        {%- do run_query(create_schema_sql) -%}
        {{ print('Created schema ' ~ database_name ~ '.' ~ schema_name ~ ' if it did not already exist.') }}
    {%- endif -%}
{% endmacro %}
