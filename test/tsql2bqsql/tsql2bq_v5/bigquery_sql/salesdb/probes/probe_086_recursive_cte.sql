-- Source: dbo.probe_086_recursive_cte (SQL Server stored procedure)
-- Probe 086: Recursive CTE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_086_recursive_cte`()
BEGIN
  -- NOTE: recursive CTE n: WITH RECURSIVE added (BigQuery stops after 500 iterations; T-SQL's default MAXRECURSION is 100, OPTION (MAXRECURSION 0) = no limit)
  WITH RECURSIVE n AS (
    SELECT
      1 AS i
    UNION ALL
    SELECT
      i + 1
    FROM n
    WHERE
      i < 5
  )
  SELECT
    i
  FROM n;
END;
