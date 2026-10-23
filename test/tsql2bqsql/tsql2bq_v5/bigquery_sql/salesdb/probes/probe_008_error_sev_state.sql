-- Source: dbo.probe_008_error_sev_state (SQL Server stored procedure)
-- Probe 008: ERROR_SEVERITY() / ERROR_STATE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_008_error_sev_state`()
BEGIN
  BEGIN
    SELECT
      DIV(1, 0);
  EXCEPTION WHEN ERROR THEN
    -- NOTE: ERROR_SEVERITY() has no BigQuery equivalent: returns NULL (the error text is in @@error.message, location in @@error.stack_trace)
    -- NOTE: ERROR_STATE() has no BigQuery equivalent: returns NULL (the error text is in @@error.message, location in @@error.stack_trace)
    SELECT
      CAST(NULL AS INT64) AS a,
      CAST(NULL AS INT64) AS b;
  END;
END;
