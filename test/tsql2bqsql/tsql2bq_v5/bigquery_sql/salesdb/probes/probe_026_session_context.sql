-- Source: dbo.probe_026_session_context (SQL Server stored procedure)
-- Probe 026: SESSION_CONTEXT()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_026_session_context`()
BEGIN
  -- TODO(review): SESSION_CONTEXT() does not exist in BigQuery: no equivalent
  SELECT
    SESSION_CONTEXT('k') AS v;
END;
