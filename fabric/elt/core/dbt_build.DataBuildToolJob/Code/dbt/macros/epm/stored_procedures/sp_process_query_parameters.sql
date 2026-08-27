{# macro:
description: Creates a SQL stored procedure that parses query parameters and inserts the callable query into its respective table.
#}
{% macro create__procedure__epm__process_query_parameters() %}
    {%- if execute -%}
        {%- set database_name = generate_epm_database() -%}
        {%- set schema_name = generate_schema_name('epm') -%}
        {%- set object_name = schema_name ~ '.process_query_parameters' -%}

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
    ,@query varchar(8000)
)
as
begin
    declare @return_query varchar(8000) = @query;
    declare @argument varchar(8000);
    declare @loop_index int = 1;

    while exists (
        select 1
        from {{ source('metadata', 'query_argument') }}
        where pipeline_run_id = @pipeline_run_id
            and orchestration_id = @orchestration_id
            and object_name = @object_name
            and argument_index = @loop_index
    )
    begin
        select @argument = argument
        from {{ source('metadata', 'query_argument') }}
        where pipeline_run_id = @pipeline_run_id
            and orchestration_id = @orchestration_id
            and object_name = @object_name
            and argument_index = @loop_index;

        set @return_query = replace(@return_query, '{' + cast(@loop_index as varchar(2)) + '}', @argument);
        set @loop_index = @loop_index + 1;
    end

    insert into {{ source('metadata', 'callable_query') }} (
        pipeline_run_id
        ,callable_query
        ,created_at
    )
    values (
        @pipeline_run_id
        ,@return_query
        ,current_timestamp
    );
end
        {%- endset -%}

        {%- do run_query(drop_sql) -%}
        {%- do run_query(create_sql) -%}
        {{ print('Created procedure ' ~ object_name) }}
    {%- endif -%}
{% endmacro %}
