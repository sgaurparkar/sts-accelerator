-- Source: dbo.probe_012_raiserror (SQL Server stored procedure)
-- Probe 012: RAISERROR(...)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_012_raiserror`()
BEGIN
  -- NOTE: RAISERROR outside TRY: T-SQL CONTINUES with the next statement, BigQuery RAISE stops the procedure (usually what was intended - check if code follows that should still run)
  RAISE USING MESSAGE = FORMAT('Bad value %d', 5);
END;
