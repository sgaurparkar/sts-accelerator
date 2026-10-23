-- Source: dbo.probe_067_top_isnull_getdate (SQL Server stored procedure)
-- Probe 067: TOP n / ISNULL / GETDATE (baseline)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_067_top_isnull_getdate`()
BEGIN
  SELECT
    Id,
    COALESCE(Name, 'x') AS n,
    CURRENT_DATETIME() AS d
  FROM `resource1.sql_server_centralasia_salesdb.T`
  ORDER BY
    Id
  LIMIT 5;
END;
