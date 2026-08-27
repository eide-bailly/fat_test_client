--@ Overrides Fabric's table materialization to not rename an intermediate, temporary relation to be the target relation, but utilize a truncate and insert instead.
--@ Source: dbt/include/fabric/macros/materializations/models/table/table.sql
{% materialization truncate_and_insert, adapter='fabric' %}
  {#- version of model that is physically materialized in the database if it exists -#}
  {%- set existing_relation = load_cached_relation(this) -%}

  {#- version of model (potentially new) coming from dbt that will be materialized as a table -#}
  {%- set target_relation = this.incorporate(type='table') %}
  {%- set create_table = true %}

  {%- set backup_relation_type = 'table' if existing_relation is none else existing_relation.type -%}
  {%- set backup_relation = make_backup_relation(base_relation=target_relation, backup_relation_type=backup_relation_type) -%}

  {# physically materialized backup table in the database if it exists #}
  {%- set preexisting_backup_relation = load_cached_relation(backup_relation) -%}

  {#- The materialized tmp_vw_relation view gets created in get_create_table_as_sql() -#}
  {% set tmp_vw_relation = target_relation.incorporate(path={"identifier": target_relation.identifier ~ '__dbt_tmp_vw'}, type='view') -%}

  {#- drop the relations if they already exist in the database -#}
  {{ drop_relation_if_exists(preexisting_backup_relation) }}
  {{ drop_relation_if_exists(tmp_vw_relation) }}

  {#- grab current table's grants config for comparision later on -#}
  {% set grant_config = config.get('grants') %}

  {{ run_hooks(pre_hooks, inside_transaction=False) }}

  {#- `BEGIN` happens here: -#}
  {{ run_hooks(pre_hooks, inside_transaction=True) }}

  {% if existing_relation is not none %}
    {% if existing_relation.is_table %}
        {%- do run_query(get_create_view_as_sql(relation=tmp_vw_relation, sql=sql)) -%}

        {#-
            expand_target_column_types() expands the current table's types to match the goal table. This is executed
                before the schema is checked for changes to prevent unnecessarily recreating the table based on a
                string length change. Tables should only be recreated for major changes (e.g., add/remove/rename column,
                change data type completely, etc.)
            example: If current table column is varchar(3) and the goal table column is varchar(6), the current table column
                will be altered to be varchar(6).

            Parameters:
            - from_relation = goal
            - to_relation = current

            NOTE: likely a dbt mistake with naming parameters. from_relation and to_relation should probably be switched.

            See dbt/adapters/sql/impl.py -> expand_column_types()
        -#}
        {% do adapter.expand_target_column_types(from_relation=tmp_vw_relation, to_relation=existing_relation) %}
        {% set schema_changes_dict = check_for_schema_changes(source_relation=tmp_vw_relation, target_relation=existing_relation) %}

        {% if schema_changes_dict['schema_changed'] %}
            {#- clone, instead of rename, the existing relation since it's a table
                Parameters
                - this_relation = new table being created
                - defer_relation = existing table being cloned from
            #}
            {{ create_or_replace_clone(this_relation=backup_relation, defer_relation=existing_relation) }}
            {{ drop_relation_if_exists(existing_relation) }}
        {% else %}
            {%- set build_sql = get_truncate_insert_sql(
                target=target_relation,
                source=tmp_vw_relation,
                dest_columns=schema_changes_dict['source_columns']) -%}
            {%- set create_table = false -%}
        {% endif %}
    {% else %}
        {#- rename the existing relation since it's not a table (e.g., view) -#}
        {{ adapter.rename_relation(from_relation=existing_relation, to_relation=backup_relation) }}
    {% endif %}
  {% endif %}

  {% if create_table %}
    {#- Existing relation is not a table or it does not exist: create it using CTAS -#}
    {%- set build_sql = get_create_table_as_sql(temporary=False, relation=target_relation, sql=sql) -%}
  {% endif %}

  {% call statement('main') -%}
    {{ build_sql }}
  {% endcall %}

  {{ run_hooks(post_hooks, inside_transaction=True) }}

  {% do apply_grants(target_relation, grant_config, should_revoke=should_revoke) %}
  {% do persist_docs(target_relation, model) %}

  {#- `COMMIT` happens here -#}
  {{ adapter.commit() }}

  {#- cleanup -#}
  {{ drop_relation_if_exists(tmp_vw_relation) }}
  {{ drop_relation_if_exists(backup_relation) }}

  {#- Add constraints including FK relation. -#}
  {{ build_model_constraints(target_relation) }}
  {{ run_hooks(post_hooks, inside_transaction=False) }}

  {{ return({'relations': [target_relation]}) }}
{% endmaterialization %}
