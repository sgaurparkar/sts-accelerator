-- Source: dbo.probe_039_format_fn (SQL Server stored procedure)
-- Probe 039: FORMAT(date, 'yyyy-MM-dd')
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_039_format_fn`()
BEGIN
  SELECT
    FORMAT_DATETIME('%Y-%m-%d', CAST(CreatedAt AS DATETIME)) AS f
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
