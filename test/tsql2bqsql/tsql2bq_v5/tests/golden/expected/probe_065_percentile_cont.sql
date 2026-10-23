-- Source: dbo.probe_065_percentile_cont (SQL Server stored procedure)
-- Probe 065: PERCENTILE_CONT ... WITHIN GROUP
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_065_percentile_cont`()
BEGIN
  SELECT DISTINCT
    PERCENTILE_CONT(Amount, 0.5) OVER (PARTITION BY Name) AS p
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
