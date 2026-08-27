{# macro:
description: Creates or updates the sp_insert_watermark_state stored procedure that records the new watermark for an object after a successful extract, computing the watermark from the destination Lakehouse table when a value is not supplied directly.
#}
{% macro create_procedure__insert_watermark_state() %}
    {%- if execute -%}
        {%- set database_name = generate_epm_database() -%}
        {%- set schema_name = generate_schema_name('epm') -%}
        {%- set object_name = schema_name ~ '.insert_watermark_state' -%}

        {%- set use_database_sql -%}
USE {{ database_name }};
        {%- endset -%}

        {%- do run_query(use_database_sql) -%}

        {%- set sql -%}
CREATE OR ALTER PROCEDURE {{ object_name }}
    @object_name NVARCHAR(256)
    , @source_system_name NVARCHAR(256)
    , @watermark_column NVARCHAR(256) = NULL
    , @watermark_value NVARCHAR(256) = NULL
    , @destination_lakehouse_name NVARCHAR(256) = NULL
    , @destination_table_schema NVARCHAR(256)
    , @destination_table_name NVARCHAR(256)
    , @watermark_type NVARCHAR(32) = NULL
    , @rows_extracted INT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @source_system_id INT;
    DECLARE @lakehouse_name NVARCHAR(256);
    DECLARE @new_watermark_value NVARCHAR(256);
    DECLARE @sql NVARCHAR(MAX);
    DECLARE @parameter_definition NVARCHAR(MAX);

    -- Look up the source system id and, unless the caller overrode it, the destination lakehouse name.
    -- The lakehouse name is NEVER a literal in this procedure -- it is always resolved from the
    -- source_system reference table (or supplied explicitly via @destination_lakehouse_name) so the
    -- procedure works unchanged across every source system and project.
    SELECT
        @source_system_id = ss.source_system_id
        , @lakehouse_name = ss.lakehouse_name
    FROM {{ ref('source_system') }} AS ss
    WHERE ss.source_system_name = @source_system_name;

    IF @source_system_id IS NULL
        BEGIN
            THROW 50000, 'sp_insert_watermark_state: no source_system row found for @source_system_name.', 1;
        END;

    IF @destination_lakehouse_name IS NOT NULL AND LTRIM(RTRIM(@destination_lakehouse_name)) <> ''
        BEGIN
            SET @lakehouse_name = @destination_lakehouse_name;
        END;

    -- Passthrough vs. compute branching: if the caller already knows the new watermark value (for
    -- example a run-timestamp captured before the extract began), use it as-is. Otherwise, when a
    -- watermark column is supplied, compute the new watermark by querying MAX(@watermark_column) from
    -- the destination Lakehouse table so the recorded state always reflects what was actually loaded.
    IF @watermark_value IS NOT NULL AND LTRIM(RTRIM(@watermark_value)) <> ''
        BEGIN
            SET @new_watermark_value = @watermark_value;
        END
    ELSE IF @watermark_column IS NOT NULL
        BEGIN
            -- Build the MAX(@watermark_column) lookup against the resolved lakehouse using QUOTENAME on
            -- every identifier segment so the lakehouse/schema/table/column names are safely tokenized
            -- into the dynamic SQL rather than concatenated as unescaped literals.
            SET @sql = N'SELECT @new_watermark_value_out = CONVERT(NVARCHAR(256), MAX(' + QUOTENAME(@watermark_column) + N')) FROM ' + QUOTENAME(@lakehouse_name) + N'.' + QUOTENAME(@destination_table_schema) + N'.' + QUOTENAME(@destination_table_name) + N';';

            SET @parameter_definition = N'@new_watermark_value_out NVARCHAR(256) OUTPUT';

            EXECUTE sp_executesql
                @sql
                , @parameter_definition
                , @new_watermark_value_out = @new_watermark_value OUTPUT;
        END;

    -- The table holds exactly one current row per (source_system_id, object_name), so the prior row is
    -- removed before the new one is written. This makes watermark row fan-out structurally impossible:
    -- an append-only history would return N rows per object after N runs, and the LEFT JOIN in
    -- sp_lookup_extract_config would then extract every table N times. Keeping only the current row also
    -- removes any ordering dependency on a dbt build to dedupe. History is deliberately not retained --
    -- run-level observability belongs in Workspace Monitoring (ItemJobEventLogs).
    DELETE FROM {{ source('epm', 'watermark_state') }}
    WHERE source_system_id = @source_system_id
        AND object_name = @object_name;

    INSERT INTO {{ source('epm', 'watermark_state') }} (
        source_system_id
        , object_name
        , watermark_value
        , watermark_type
        , extract_timestamp_utc
        , rows_extracted
    )
    VALUES (
        @source_system_id
        , @object_name
        , @new_watermark_value
        , @watermark_type
        , SYSUTCDATETIME()
        , @rows_extracted
    );
END;
        {%- endset -%}

        {%- do run_query(sql) -%}
        {{ print('Created procedure ' ~ object_name) }}
    {%- endif -%}
{% endmacro %}
