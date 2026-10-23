-- Source: dbo.probe_022_host_app (SQL Server stored procedure)
-- Probe 022: HOST_NAME() / APP_NAME()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_022_host_app`()
BEGIN
  -- TODO(review): HOST_NAME: no equivalent
  -- TODO(review): APP_NAME: no equivalent
  SELECT
    HOST_NAME() AS h,
    APP_NAME() AS a;
END;
