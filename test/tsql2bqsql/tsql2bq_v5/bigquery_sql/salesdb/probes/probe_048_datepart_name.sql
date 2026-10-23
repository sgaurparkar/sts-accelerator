-- Source: dbo.probe_048_datepart_name (SQL Server stored procedure)
-- Probe 048: DATEPART() / DATENAME()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_048_datepart_name`()
BEGIN
  SELECT
    EXTRACT(DAYOFWEEK FROM CreatedAt) AS a,
    FORMAT_TIMESTAMP('%B', CAST(CreatedAt AS TIMESTAMP)) AS b
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
