-- Source: dbo.probe_097_delete_top (SQL Server stored procedure)
-- Probe 097: DELETE TOP (n)
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_097_delete_top`()
BEGIN
  -- TODO(review): DELETE TOP (n) has no BigQuery equivalent: rewrite as DELETE ... WHERE key IN (SELECT key FROM ... ORDER BY ... LIMIT n)
  -- TODO(manual): DELETE TOP (n) is not supported
  --   DELETE TOP (10) FROM dbo.T WHERE Id > 0
END;
