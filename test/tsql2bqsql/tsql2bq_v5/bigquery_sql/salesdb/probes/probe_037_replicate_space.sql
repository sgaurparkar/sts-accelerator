-- Source: dbo.probe_037_replicate_space (SQL Server stored procedure)
-- Probe 037: REPLICATE() / SPACE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_037_replicate_space`()
BEGIN
  SELECT
    REPEAT('a', 3) AS r,
    REPEAT(' ', 2) AS s;
END;
