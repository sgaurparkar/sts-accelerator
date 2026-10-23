-- Source: dbo.probe_096_update_top (SQL Server stored procedure)
-- Probe 096: UPDATE TOP (n)
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_096_update_top`()
BEGIN
  -- TODO(review): UPDATE TOP (n) has no BigQuery equivalent: rewrite as UPDATE ... WHERE key IN (SELECT key FROM ... ORDER BY ... LIMIT n)
  -- TODO(manual): UPDATE TOP (n) is not supported
  --   UPDATE TOP (10) dbo.T SET Name = 'x'
END;
