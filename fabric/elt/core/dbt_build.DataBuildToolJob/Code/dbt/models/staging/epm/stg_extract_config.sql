-- Staging model: active extract configuration with source system context.
-- Consumed by extract pipelines to determine which objects to extract and how.
-- Only active source systems and active objects are included.

SELECT
    ec.extract_config_id
    , ec.orchestration_id
    , ec.source_system_id
    , ss.source_system_name
    , ss.source_type
    , ss.lakehouse_name
    , ec.object_name
    , ec.watermark_column
    , ec.watermark_type
    , ec.load_type
    , ec.is_active
    , ec.destination_table_schema
    , ec.destination_table_name
    , ec.destination_table_schema + '.' + ec.destination_table_name AS destination_full_name
    , ec.update_strategy
    , ec.query_params
    , ec.parameter_config
    , ec.primary_key_columns
    , ec.custom_config
FROM {{ ref('source_system') }} AS ss
INNER JOIN {{ ref('extract_config') }} AS ec
    ON ss.source_system_id = ec.source_system_id
WHERE ss.is_active = 1
    AND ec.is_active = 1
