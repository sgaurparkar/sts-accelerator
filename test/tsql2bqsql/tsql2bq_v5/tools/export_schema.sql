-- Run this on SQL Server (once per database) and save the result grid as columns.csv
-- (with the header row).  Then:  python tools/schema_to_config.py columns.csv
--
-- Why: BigQuery is strict about types.  `IsActive = 1` fails when IsActive is BOOL (a migrated BIT), DATE_ADD only
-- accepts DATE, `int / int` must become DIV() (T-SQL integer division) and CAST(decimal AS INT) must truncate.
-- Knowing which columns are BIT / DATE / DATETIME / integer / decimal lets the converter emit the right code.
-- v5: integer and decimal types are exported too (v4 only exported BIT and date types, so column divisions were
-- never converted automatically).
-- Columns that have different types in different tables are skipped (list them by hand in config.json).
SELECT COLUMN_NAME, MIN(DATA_TYPE) AS DATA_TYPE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE DATA_TYPE IN ('bit', 'date', 'datetime', 'datetime2', 'smalldatetime', 'datetimeoffset',
                    'int', 'bigint', 'smallint', 'tinyint',
                    'decimal', 'numeric', 'money', 'smallmoney', 'float', 'real')
GROUP BY COLUMN_NAME
HAVING COUNT(DISTINCT DATA_TYPE) = 1
ORDER BY COLUMN_NAME;

-- Columns whose type differs between tables (not exported above - decide per column and add to config.json):
-- SELECT COLUMN_NAME, STRING_AGG(TABLE_NAME + ':' + DATA_TYPE, ', ') AS types
-- FROM INFORMATION_SCHEMA.COLUMNS GROUP BY COLUMN_NAME HAVING COUNT(DISTINCT DATA_TYPE) > 1;
