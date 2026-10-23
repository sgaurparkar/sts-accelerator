-- Source: dbo.probe_024_server_vars (SQL Server stored procedure)
-- Probe 024: @@SPID / @@SERVERNAME / @@VERSION
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_024_server_vars`()
BEGIN
  -- TODO(review): @@SPID: no equivalent
  -- TODO(review): @@SERVERNAME: no equivalent
  -- TODO(review): @@VERSION: no equivalent
  SELECT
    @@SPID AS a,
    @@SERVERNAME AS b,
    @@VERSION AS c;
END;
