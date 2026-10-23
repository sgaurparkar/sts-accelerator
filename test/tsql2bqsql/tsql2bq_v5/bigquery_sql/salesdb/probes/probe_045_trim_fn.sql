-- Source: dbo.probe_045_trim_fn (SQL Server stored procedure)
-- Probe 045: LTRIM / RTRIM / TRIM
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_045_trim_fn`()
BEGIN
  SELECT
    LTRIM(RTRIM(Name)) AS t,
    TRIM(Name) AS u
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
