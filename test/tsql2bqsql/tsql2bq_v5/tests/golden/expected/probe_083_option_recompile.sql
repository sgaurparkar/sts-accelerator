-- Source: dbo.probe_083_option_recompile (SQL Server stored procedure)
-- Probe 083: OPTION (RECOMPILE) query hint
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_083_option_recompile`()
BEGIN
  SELECT
    Id
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
