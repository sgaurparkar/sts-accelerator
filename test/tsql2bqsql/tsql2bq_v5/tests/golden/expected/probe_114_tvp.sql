-- Source: dbo.probe_114_tvp (SQL Server stored procedure)
-- Probe 114: Table-valued parameter (READONLY)
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_114_tvp`(
  -- TODO(manual): IN p_items ARRAY<STRUCT<...>>  -- T-SQL: @items dbo.ItemList READONLY
)
BEGIN
  SELECT
    COUNT(*) AS n
  FROM UNNEST(p_items);
END;
