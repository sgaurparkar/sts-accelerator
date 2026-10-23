-- Source: dbo.probe_071_pivot (SQL Server stored procedure)
-- Probe 071: PIVOT
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_071_pivot`()
BEGIN
  -- NOTE: PIVOT: converted to BigQuery PIVOT syntax; compare the result columns once
  SELECT
    *
  FROM (
    SELECT
      Name,
      Amount
    FROM `resource1.sql_server_centralasia_salesdb.T`
  ) AS s
  PIVOT(SUM(Amount) FOR Name IN ('a' AS a, 'b' AS b)) AS p;
END;
