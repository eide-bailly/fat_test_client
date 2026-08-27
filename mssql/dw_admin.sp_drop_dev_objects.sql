-- sp_drop_dev_objects: drops all user-created schemas and tables in the warehouse
-- that match a given schema prefix. Used to clean up dev/test objects without
-- touching reference/epm schemas or production data.
--
-- Parameters:
--   @schema_prefix  VARCHAR(64) — prefix to match (e.g. 'dev_', 'test_')
--   @debug          BIT         — 1 = print DROP statements without executing; 0 = execute

CREATE OR ALTER PROCEDURE dbo.sp_drop_dev_objects
    @schema_prefix VARCHAR(64)
    , @debug BIT = 0
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @sql NVARCHAR(MAX);
    DECLARE @object_name NVARCHAR(256);
    DECLARE @schema_name NVARCHAR(128);

    -- Protected schemas that must never be dropped
    DECLARE @protected_schemas TABLE (schema_name NVARCHAR(128));
    INSERT INTO @protected_schemas VALUES ('dbo'), ('epm'), ('reference'), ('sys'), ('INFORMATION_SCHEMA');

    -- Drop all tables in matching schemas
    DECLARE table_cursor CURSOR FOR
    SELECT
        s.name AS schema_name
        , t.name AS table_name
    FROM sys.tables AS t
    INNER JOIN sys.schemas AS s ON t.schema_id = s.schema_id
    WHERE s.name LIKE @schema_prefix + '%'
        AND s.name NOT IN (SELECT p.schema_name FROM @protected_schemas AS p)
    ORDER BY s.name, t.name;

    OPEN table_cursor;
    FETCH NEXT FROM table_cursor INTO @schema_name, @object_name;

    WHILE @@FETCH_STATUS = 0
        BEGIN
            SET @sql = N'DROP TABLE IF EXISTS [' + @schema_name + N'].[' + @object_name + N'];';
            IF @debug = 1
                PRINT @sql;
            ELSE
                EXEC sp_executesql @sql;

            FETCH NEXT FROM table_cursor INTO @schema_name, @object_name;
        END;

    CLOSE table_cursor;
    DEALLOCATE table_cursor;

    -- Drop matching schemas (after tables are gone)
    DECLARE schema_cursor CURSOR FOR
    SELECT name FROM sys.schemas
    WHERE name LIKE @schema_prefix + '%'
        AND name NOT IN (SELECT p.schema_name FROM @protected_schemas AS p)
    ORDER BY name;

    OPEN schema_cursor;
    FETCH NEXT FROM schema_cursor INTO @schema_name;

    WHILE @@FETCH_STATUS = 0
        BEGIN
            SET @sql = N'DROP SCHEMA IF EXISTS [' + @schema_name + N'];';
            IF @debug = 1
                PRINT @sql;
            ELSE
                EXEC sp_executesql @sql;

            FETCH NEXT FROM schema_cursor INTO @schema_name;
        END;

    CLOSE schema_cursor;
    DEALLOCATE schema_cursor;

    IF @debug = 1
        PRINT 'Debug mode: no objects were dropped.';
    ELSE
        PRINT 'Dropped all objects matching schema prefix: ' + @schema_prefix;
END;
