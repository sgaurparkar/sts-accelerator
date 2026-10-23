-- Source: dbo.probe_007_error_number (SQL Server stored procedure)
-- Probe 007: ERROR_NUMBER()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_007_error_number`()
BEGIN
  BEGIN
    SELECT
      DIV(1, 0);
  EXCEPTION WHEN ERROR THEN
    -- NOTE: ERROR_NUMBER() has no BigQuery equivalent: returns NULL (the error text is in @@error.message, location in @@error.stack_trace)
    SELECT
      CAST(NULL AS INT64) AS n;
  END;
END;
