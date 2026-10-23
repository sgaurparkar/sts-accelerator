-- Source: dbo.probe_051_at_time_zone (SQL Server stored procedure)
-- Probe 051: AT TIME ZONE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_051_at_time_zone`()
BEGIN
  -- NOTE: x AT TIME ZONE z -> TIMESTAMP(x, z), assuming x is a DATETIME (wall-clock time in z)
  SELECT
    TIMESTAMP(CreatedAt, 'UTC') AS t
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
