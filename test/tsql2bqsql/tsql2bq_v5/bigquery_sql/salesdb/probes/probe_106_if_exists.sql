-- Source: dbo.probe_106_if_exists (SQL Server stored procedure)
-- Probe 106: IF EXISTS (...) ... ELSE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_106_if_exists`()
BEGIN
  IF EXISTS(SELECT 1 FROM `resource1.sql_server_centralasia_salesdb.T` WHERE Id = 1) THEN
    SELECT
      1 AS a;
  ELSE
    SELECT
      0 AS a;
  END IF;
END;
