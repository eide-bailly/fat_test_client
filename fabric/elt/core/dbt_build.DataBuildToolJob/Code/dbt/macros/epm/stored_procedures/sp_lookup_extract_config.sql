{# macro:
description: Creates or updates the sp_lookup_extract_config stored procedure that retrieves extraction configuration -- including the ready-to-run extract_query -- with optional filtering by orchestration_id, source_system_name, object names, and debug mode.
#}
{% macro create_procedure__lookup_extract_config() %}
    {%- if execute -%}
        {%- set database_name = generate_epm_database() -%}
        {%- set schema_name = generate_schema_name('epm') -%}
        {%- set object_name = schema_name ~ '.lookup_extract_config' -%}

        {%- set use_database_sql -%}
USE {{ database_name }};
        {%- endset -%}

        {%- do run_query(use_database_sql) -%}

        {%- set sql -%}
CREATE OR ALTER PROCEDURE {{ object_name }}
    @object_names NVARCHAR(MAX) = NULL
    , @source_system_name NVARCHAR(256) = NULL
    , @orchestration_id INT = NULL
    , @is_active BIT = 1
    , @debug BIT = 0
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @sql NVARCHAR(MAX);
    DECLARE @object_names_clean NVARCHAR(MAX);

    -- Clean up object_names: trim and handle empty string
    SET @object_names_clean = NULLIF(LTRIM(RTRIM(@object_names)), '');

    -- Build the dynamic SQL query. The configuration rows are assembled in a CTE so the
    -- extract_query column can be derived from select_statement without repeating its expression.
    SET @sql = N'
    WITH cfg AS (
        SELECT
            ec.orchestration_id
            , ec.source_system_id
            , ec.object_name
            , orch.load_type
            , ss.lakehouse_name
            , orch.source_system_name
            , orch.schedule
            , ec.destination_table_name
            , ec.destination_table_schema
            , ec.update_strategy
            , ec.watermark_column
            , ec.watermark_type
            , ws.watermark_value
            , COALESCE(NULLIF(ec.query_params, ''''), ''SELECT * FROM '' + ec.object_name) AS select_statement
            , LOWER(ec.destination_table_schema + ''/'' + ec.destination_table_name + CASE WHEN orch.load_type <> ''full'' THEN ''__incremental'' ELSE '''' END) AS storage_folder_path
            , ec.destination_table_name + ''.'' + ss.file_extension AS storage_file_name
            , ss.file_extension
            , ec.query_params
            -- Note: parameter_config currently only substitutes {lakehouse_name}. Multi-placeholder support is a future enhancement.
            , REPLACE(ec.parameter_config, ''{lakehouse_name}'', ss.lakehouse_name) AS parameter_statements
            , ec.primary_key_columns
            , ec.custom_config
            , ec.is_active
            , orch.is_active AS is_orchestration_active
        FROM {{ ref('extract_config') }} AS ec
        LEFT JOIN {{ ref('orchestration') }} AS orch
            ON ec.orchestration_id = orch.orchestration_id
        LEFT JOIN {{ ref('source_system') }} AS ss
            ON ss.source_system_name = orch.source_system_name
        LEFT JOIN {{ source('epm', 'watermark_state') }} AS ws
            ON ws.source_system_id = ec.source_system_id
            AND ws.object_name = ec.object_name
        WHERE ec.is_active = @is_active_param
            AND (
                (@orchestration_id_param IS NOT NULL AND ec.orchestration_id = @orchestration_id_param)
                OR (@orchestration_id_param IS NULL AND ss.source_system_name = @source_system_name_param)
            )
    ';

    -- Add object_names condition if provided
    IF @object_names_clean IS NOT NULL
        BEGIN
            SET @sql = @sql + N'
            AND ec.object_name IN (
                SELECT TRIM(value)
                FROM STRING_SPLIT(@object_names_param, '','')
                WHERE TRIM(value) <> ''''
            )';
        END;

    -- A full extract (overwrite or eom strategy, or a missing watermark column/value) returns the base
    -- select statement unchanged. An incremental extract (append/merge with a real watermark) wraps the
    -- base select statement as a subquery and adds a WHERE clause filtering rows greater than the last
    -- watermark value. The watermark column identifier is safely bracket-quoted with QUOTENAME(), and
    -- embedded single quotes in the watermark value are escaped (doubled) before being concatenated into
    -- the quoted literal, to prevent SQL injection. Quotes below are doubled a second time because this
    -- expression is itself embedded in the dynamic SQL string literal.
    SET @sql = @sql + N'
    )
    SELECT
        cfg.orchestration_id
        , cfg.source_system_id
        , cfg.object_name
        , cfg.load_type
        , cfg.lakehouse_name
        , cfg.source_system_name
        , cfg.schedule
        , cfg.destination_table_name
        , cfg.destination_table_schema
        , cfg.update_strategy
        , cfg.watermark_column
        , cfg.watermark_type
        , cfg.watermark_value
        , cfg.select_statement
        , cfg.storage_folder_path
        , cfg.storage_file_name
        , cfg.file_extension
        , cfg.query_params
        , cfg.parameter_statements
        , cfg.primary_key_columns
        , cfg.custom_config
        , cfg.is_active
        , cfg.is_orchestration_active
        , CASE
            WHEN cfg.update_strategy IN (''overwrite'', ''eom'')
                OR cfg.watermark_column IS NULL
                OR LTRIM(RTRIM(cfg.watermark_column)) = ''''
                OR cfg.watermark_value IS NULL
                OR cfg.watermark_value = ''''
                THEN cfg.select_statement
            ELSE ''SELECT * FROM ('' + cfg.select_statement + '') AS src WHERE src.'' + QUOTENAME(cfg.watermark_column) + '' > '''''' + REPLACE(cfg.watermark_value, '''''''', '''''''''''') + ''''''''
        END AS extract_query
    FROM cfg
    ORDER BY cfg.object_name;
    ';

    -- Debug mode: print the query instead of executing
    IF @debug = 1
        BEGIN
            PRINT N'-- Debug Mode: Generated Query';
            PRINT N'-- @source_system_name = ' + ISNULL(@source_system_name, 'NULL');
            PRINT N'-- @orchestration_id = ' + ISNULL(CAST(@orchestration_id AS NVARCHAR(MAX)), 'NULL');
            PRINT N'-- @object_names = ' + ISNULL(@object_names, 'NULL');
            PRINT N'-- @is_active = ' + CAST(@is_active AS NVARCHAR(MAX));
            PRINT N'';
            PRINT @sql;
        END
    ELSE
        BEGIN
            -- Execute the dynamic SQL with parameter bindings
            EXECUTE sp_executesql
                @sql
                , N'@source_system_name_param NVARCHAR(256), @orchestration_id_param INT, @object_names_param NVARCHAR(MAX), @is_active_param BIT'
                , @source_system_name_param = @source_system_name
                , @orchestration_id_param = @orchestration_id
                , @object_names_param = @object_names_clean
                , @is_active_param = @is_active;
        END;
END;
        {%- endset -%}

        {%- do run_query(sql) -%}
        {{ print('Created procedure ' ~ object_name) }}
    {%- endif -%}
{% endmacro %}
