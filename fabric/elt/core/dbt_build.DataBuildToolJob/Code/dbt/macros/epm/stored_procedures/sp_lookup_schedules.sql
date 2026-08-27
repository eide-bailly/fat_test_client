{# macro:
description: Creates a SQL stored procedure that retrieves schedules and pipeline ids
#}
{% macro create__procedure__epm__lookup_schedules() %}
    {%- if execute -%}
        {%- set database_name = generate_epm_database() -%}
        {%- set schema_name = generate_schema_name('epm') -%}
        {%- set object_name = schema_name ~ '.lookup_schedules' -%}

        {%- set use_database_sql -%}
USE {{ database_name }};
        {%- endset -%}

        {%- do run_query(use_database_sql) -%}

        {%- set create_sql -%}
CREATE OR ALTER PROCEDURE {{ object_name }} (
    @schedule VARCHAR(50)
    , @pipeline_names VARCHAR(8000) = NULL

)
AS
BEGIN
    DECLARE @pipeline_names_clean VARCHAR(8000);

    -- A Fabric pipeline parameter cannot pass SQL NULL -- an unset parameter arrives as an empty
    -- string. Collapse both NULL and '' to NULL so the default invocation means "all pipelines".
    SET @pipeline_names_clean = NULLIF(LTRIM(RTRIM(@pipeline_names)), '');

    SELECT
        o.orchestration_id
        , o.pipeline AS pipeline_name
        , p.id AS pipeline_id
        , o.source_system_name
        , o.load_type
    FROM {{ ref('orchestration') }} AS o
    INNER JOIN {{ ref('pipelines') }} AS p
        ON o.pipeline = p.display_name
    WHERE o.is_active = 1
        AND o.schedule = @schedule
        AND (
            @pipeline_names_clean IS NULL
            OR o.pipeline IN (
                SELECT TRIM(value)
                FROM STRING_SPLIT(@pipeline_names_clean, ',')
                WHERE TRIM(value) <> ''
            )
        )
END
        {%- endset -%}

        {%- do run_query(create_sql) -%}
        {{ print('Created procedure ' ~ object_name) }}
    {%- endif -%}
{% endmacro %}
