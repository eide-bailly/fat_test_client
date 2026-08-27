{# macro:
description: Creates a SQL stored procedure to insert a record into the query argument table.
#}
{% macro create_procedure__epm__insert_query_argument() %}
    {%- if execute -%}
        {%- set database_name = generate_epm_database() -%}
        {%- set schema_name = generate_schema_name('epm') -%}
        {%- set object_name = schema_name ~ '.insert_query_argument' -%}

        {%- set use_database_sql -%}
USE {{ database_name }};
        {%- endset -%}

        {%- do run_query(use_database_sql) -%}

        {%- set drop_sql -%}
            drop procedure if exists {{ object_name }};
        {%- endset -%}

        {%- set create_sql -%}
        create procedure {{ object_name }} (
    @pipeline_run_id varchar(50)
    ,@orchestration_id int
    ,@object_name varchar(128)
    ,@argument_index smallint
    ,@argument_query nvarchar(4000) -- must be nvarchar to work with sp_executesql
)
as
begin
    declare @argument varchar(100);
    declare @parameter_definition nvarchar(50) = '@argument varchar(100) output' -- must be nvarchar to work with sp_executesql
    execute sp_executesql @statement=@argument_query, @arguments=@parameter_definition, @argument=@argument output;

    insert into {{ source('metadata', 'query_argument') }} (
        pipeline_run_id
        ,orchestration_id
        ,object_name
        ,argument_index
        ,argument
        ,created_at
    )
    values (
        @pipeline_run_id
        ,@orchestration_id
        ,@object_name
        ,@argument_index
        ,@argument
        ,current_timestamp
    );
end
        {%- endset -%}

        {%- do run_query(drop_sql) -%}
        {%- do run_query(create_sql) -%}
        {{ print('Created procedure ' ~ object_name) }}
    {%- endif -%}
{%- endmacro %}
