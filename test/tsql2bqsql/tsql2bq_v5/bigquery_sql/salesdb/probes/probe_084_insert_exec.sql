-- Source: dbo.probe_084_insert_exec (SQL Server stored procedure)
-- Probe 084: INSERT ... EXEC proc
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_084_insert_exec`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_r (
    a INT64
  );
  -- TODO(review): INSERT ... EXEC proc: BigQuery cannot insert a procedure's result set - let the called procedure write into a TEMP TABLE (temp tables are visible to the caller in the same script) or turn it into a TABLE FUNCTION and INSERT ... SELECT from it
  -- TODO(manual): INSERT ... EXEC is not supported
  --   INSERT INTO #r EXEC dbo.usp_Other
END;
