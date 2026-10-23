-- Source: dbo.probe_029_quotename (SQL Server stored procedure)
-- Probe 029: QUOTENAME()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_029_quotename`()
BEGIN
  -- TODO(review): QUOTENAME: no equivalent
  SELECT
    QUOTENAME('a b') AS q;
END;
