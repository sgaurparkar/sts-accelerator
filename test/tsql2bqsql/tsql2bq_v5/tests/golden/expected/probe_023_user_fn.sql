-- Source: dbo.probe_023_user_fn (SQL Server stored procedure)
-- Probe 023: SUSER_SNAME() / SYSTEM_USER / USER_NAME()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_023_user_fn`()
BEGIN
  -- NOTE: SUSER_SNAME() / SYSTEM_USER / CURRENT_USER -> SESSION_USER(): returns the Google account e-mail (user or service account), not the SQL Server login
  SELECT
    SESSION_USER() AS a,
    SESSION_USER() AS b,
    SESSION_USER() AS c;
END;
