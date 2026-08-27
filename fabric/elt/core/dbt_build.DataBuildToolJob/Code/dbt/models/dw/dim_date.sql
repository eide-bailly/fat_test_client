{{ config(materialized='table') }}

-- Generates one row per calendar day from dim_date_start_date to dim_date_end_date.
-- The cross-join CTE produces 10 000 rows, covering ~27 years from any start date.
-- Date range is controlled by vars in dbt_project.yml.
-- DATENAME() returns nvarchar, which Fabric Warehouse does not support -- all string
-- columns are explicitly CAST to VARCHAR.
WITH
    nums AS (
        SELECT ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS day_offset
        FROM (VALUES (0), (0), (0), (0), (0), (0), (0), (0), (0), (0)) AS l0 (c)
        CROSS JOIN (VALUES (0), (0), (0), (0), (0), (0), (0), (0), (0), (0)) AS l1 (c)
        CROSS JOIN (VALUES (0), (0), (0), (0), (0), (0), (0), (0), (0), (0)) AS l2 (c)
        CROSS JOIN (VALUES (0), (0), (0), (0), (0), (0), (0), (0), (0), (0)) AS l3 (c)
    )

    , date_spine AS (
        SELECT
            DATEADD(
                DAY
                , day_offset
                , CAST('{{ var("dim_date_start_date", "2015-01-01") }}' AS DATE)
            ) AS date_day
        FROM nums
        WHERE day_offset <= DATEDIFF(
                DAY
                , CAST('{{ var("dim_date_start_date", "2015-01-01") }}' AS DATE)
                , CAST('{{ var("dim_date_end_date", "2035-12-31") }}' AS DATE)
            )
    )

SELECT
    date_day
    , CAST(
        DATEPART(YEAR, date_day) * 10000
        + DATEPART(MONTH, date_day) * 100
        + DATEPART(DAY, date_day) AS INT
    ) AS date_key

    -- Year / quarter / month
    , DATEPART(YEAR, date_day) AS year_number
    , DATEPART(QUARTER, date_day) AS quarter_of_year
    , DATEPART(MONTH, date_day) AS month_of_year
    , CAST(DATENAME(MONTH, date_day) AS VARCHAR(10)) AS month_name
    , CAST(LEFT(DATENAME(MONTH, date_day), 3) AS VARCHAR(3)) AS month_name_short

    -- Week
    , DATEPART(ISO_WEEK, date_day) AS iso_week_of_year

    -- Day
    , DATEPART(DAYOFYEAR, date_day) AS day_of_year
    , DATEPART(DAY, date_day) AS day_of_month
    -- ISO weekday (1=Monday...7=Sunday), independent of SET DATEFIRST
    , CASE DATENAME(WEEKDAY, date_day)
        WHEN 'Monday' THEN 1
        WHEN 'Tuesday' THEN 2
        WHEN 'Wednesday' THEN 3
        WHEN 'Thursday' THEN 4
        WHEN 'Friday' THEN 5
        WHEN 'Saturday' THEN 6
        WHEN 'Sunday' THEN 7
    END AS day_of_week
    , CAST(DATENAME(WEEKDAY, date_day) AS VARCHAR(10)) AS day_of_week_name
    , CAST(LEFT(DATENAME(WEEKDAY, date_day), 3) AS VARCHAR(3)) AS day_of_week_name_short

    -- Month boundaries
    , CAST(
        DATEFROMPARTS(DATEPART(YEAR, date_day), DATEPART(MONTH, date_day), 1) AS DATE
    ) AS first_day_of_month
    , CAST(EOMONTH(date_day) AS DATE) AS last_day_of_month

    -- Quarter boundaries
    , CAST(
        DATEFROMPARTS(
            DATEPART(YEAR, date_day)
            , (DATEPART(QUARTER, date_day) - 1) * 3 + 1
            , 1
        ) AS DATE
    ) AS first_day_of_quarter
    , CAST(
        EOMONTH(DATEFROMPARTS(
            DATEPART(YEAR, date_day)
            , (DATEPART(QUARTER, date_day) - 1) * 3 + 3
            , 1
        )) AS DATE
    ) AS last_day_of_quarter

    -- Year boundaries
    , CAST(DATEFROMPARTS(DATEPART(YEAR, date_day), 1, 1) AS DATE) AS first_day_of_year
    , CAST(DATEFROMPARTS(DATEPART(YEAR, date_day), 12, 31) AS DATE) AS last_day_of_year

    -- ISO week boundaries (Monday-Sunday)
    , CAST(
        DATEADD(
            DAY
            , 1 - CASE DATENAME(WEEKDAY, date_day)
                WHEN 'Monday' THEN 1
                WHEN 'Tuesday' THEN 2
                WHEN 'Wednesday' THEN 3
                WHEN 'Thursday' THEN 4
                WHEN 'Friday' THEN 5
                WHEN 'Saturday' THEN 6
                WHEN 'Sunday' THEN 7
            END
            , date_day
        ) AS DATE
    ) AS first_day_of_iso_week
    , CAST(
        DATEADD(
            DAY
            , 7 - CASE DATENAME(WEEKDAY, date_day)
                WHEN 'Monday' THEN 1
                WHEN 'Tuesday' THEN 2
                WHEN 'Wednesday' THEN 3
                WHEN 'Thursday' THEN 4
                WHEN 'Friday' THEN 5
                WHEN 'Saturday' THEN 6
                WHEN 'Sunday' THEN 7
            END
            , date_day
        ) AS DATE
    ) AS last_day_of_iso_week

    -- Integer composite keys (useful for BI slicer hierarchies and range filters)
    , CAST(DATEPART(YEAR, date_day) * 100 + DATEPART(MONTH, date_day) AS INT) AS year_month_number
    , CAST(DATEPART(YEAR, date_day) * 10 + DATEPART(QUARTER, date_day) AS INT) AS year_quarter_number

    -- Relative offset from today (negative = past, 0 = today, positive = future)
    , DATEDIFF(DAY, CAST(GETDATE() AS DATE), date_day) AS days_from_today
    , DATEDIFF(MONTH, CAST(GETDATE() AS DATE), date_day) AS months_from_today
    , DATEDIFF(YEAR, CAST(GETDATE() AS DATE), date_day) AS years_from_today

    -- Current-period flags
    , CAST(CASE WHEN date_day = CAST(GETDATE() AS DATE) THEN 1 ELSE 0 END AS BIT) AS is_current_day
    , CAST(
        CASE
            WHEN DATEPART(YEAR, date_day) = DATEPART(YEAR, GETDATE())
                AND DATEPART(MONTH, date_day) = DATEPART(MONTH, GETDATE())
                THEN 1
            ELSE 0
        END AS BIT
    ) AS is_current_month
    , CAST(
        CASE
            WHEN DATEPART(YEAR, date_day) = DATEPART(YEAR, GETDATE())
                AND DATEPART(QUARTER, date_day) = DATEPART(QUARTER, GETDATE())
                THEN 1
            ELSE 0
        END AS BIT
    ) AS is_current_quarter
    , CAST(
        CASE WHEN DATEPART(YEAR, date_day) = DATEPART(YEAR, GETDATE()) THEN 1 ELSE 0 END AS BIT
    ) AS is_current_year

    -- Weekend / weekday flags
    , CAST(
        CASE DATENAME(WEEKDAY, date_day) WHEN 'Saturday' THEN 1 WHEN 'Sunday' THEN 1 ELSE 0 END
        AS BIT
    ) AS is_weekend
    , CAST(
        CASE DATENAME(WEEKDAY, date_day) WHEN 'Saturday' THEN 0 WHEN 'Sunday' THEN 0 ELSE 1 END
        AS BIT
    ) AS is_weekday

FROM date_spine
