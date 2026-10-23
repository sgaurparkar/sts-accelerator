-- Source: dbo.probe_055_try_convert (SQL Server stored procedure)
-- Probe 055: TRY_CONVERT() / TRY_CAST()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_055_try_convert`()
BEGIN
  SELECT
    SAFE_CAST(Name AS INT64) AS a,
    SAFE_CAST(Name AS INT64) AS b
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
