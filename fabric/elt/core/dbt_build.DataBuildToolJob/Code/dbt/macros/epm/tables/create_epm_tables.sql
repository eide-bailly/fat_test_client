{# macro:
description: Creates the callable_query table in the EPM schema if it does not already exist.
#}
{% macro create_table__epm__callable_query() %}
    {%- if execute -%}
        {%- set schema_name = generate_schema_name('epm') -%}
        {%- set object_name = schema_name ~ '.' ~ 'callable_query' -%}

        {%- set use_database_sql -%}
USE {{ generate_epm_database() }};
        {%- endset -%}

        {%- do run_query(use_database_sql) -%}

        {%- set create_sql -%}
IF OBJECT_ID('{{ object_name }}', 'U') IS NULL
BEGIN
    CREATE TABLE {{ object_name }} (
        callable_query_id BIGINT IDENTITY NOT NULL
        ,pipeline_run_id VARCHAR(50) NOT NULL
        ,callable_query VARCHAR(MAX) NOT NULL
        ,created_at DATETIME2(6) NOT NULL
    );
END
        {%- endset -%}

        {%- do run_query(create_sql) -%}
        {{ print('Created table ' ~ object_name ~ ' if it did not already exist.') }}
    {%- endif -%}
{%- endmacro %}

{# macro:
description: Creates the query_argument table in the EPM schema if it does not already exist.
#}
{% macro create_table__epm__query_argument() %}
    {%- if execute -%}
        {%- set schema_name = generate_schema_name('epm') -%}
        {%- set object_name = schema_name ~ '.' ~ 'query_argument' -%}

        {%- set use_database_sql -%}
USE {{ generate_epm_database() }};
        {%- endset -%}

        {%- do run_query(use_database_sql) -%}

        {%- set create_sql -%}
IF OBJECT_ID('{{ object_name }}', 'U') IS NULL
BEGIN
    CREATE TABLE {{ object_name }} (
        query_argument_id BIGINT IDENTITY NOT NULL
        ,pipeline_run_id VARCHAR(50) NOT NULL
        ,orchestration_id INT NOT NULL
        ,object_name VARCHAR(128) NOT NULL
        ,argument_index SMALLINT NOT NULL
        ,argument VARCHAR(100) NULL
        ,created_at DATETIME2(6) NOT NULL
    );
END
        {%- endset -%}

        {%- do run_query(create_sql) -%}
        {{ print('Created table ' ~ object_name ~ ' if it did not already exist.') }}
    {%- endif -%}
{%- endmacro %}

{# macro:
description: Creates the watermark_state table in the EPM schema if it does not already exist.
#}
{% macro create_table__epm__watermark_state() %}
    {%- if execute -%}
        {%- set schema_name = generate_schema_name('epm') -%}
        {%- set object_name = schema_name ~ '.' ~ 'watermark_state' -%}

        {%- set use_database_sql -%}
USE {{ generate_epm_database() }};
        {%- endset -%}

        {%- do run_query(use_database_sql) -%}

        {%- set create_sql -%}
IF OBJECT_ID('{{ object_name }}', 'U') IS NULL
BEGIN
    CREATE TABLE {{ object_name }} (
        source_system_id INT NOT NULL
        ,object_name VARCHAR(256) NOT NULL
        ,watermark_value VARCHAR(256) NULL
        ,watermark_type VARCHAR(32) NULL
        ,extract_timestamp_utc DATETIME2(6) NULL
        ,rows_extracted INT NULL
    );
END
        {%- endset -%}

        {%- do run_query(create_sql) -%}
        {{ print('Created table ' ~ object_name ~ ' if it did not already exist.') }}
    {%- endif -%}
{%- endmacro %}
