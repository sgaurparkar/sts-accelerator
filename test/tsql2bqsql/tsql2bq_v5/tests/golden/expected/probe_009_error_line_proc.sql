-- Source: dbo.probe_009_error_line_proc (SQL Server stored procedure)
-- Probe 009: ERROR_LINE() / ERROR_PROCEDURE()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_009_error_line_proc`()
BEGIN
  BEGIN
    SELECT
      DIV(1, 0);
  EXCEPTION WHEN ERROR THEN
    -- NOTE: ERROR_LINE() -> @@error.stack_trace[SAFE_OFFSET(0)].line (a line number of the BigQuery script, not of the original T-SQL)
    -- NOTE: ERROR_PROCEDURE() replaced by the procedure name 'probe_009_error_line_proc' (BigQuery has no equivalent; if the error came from a called procedure, the call stack is in @@error.stack_trace)
    SELECT
      @@error.stack_trace[SAFE_OFFSET(0)].line AS a,
      'probe_009_error_line_proc' AS b;
  END;
END;
