-- Source: dbo.probe_006_newid (SQL Server stored procedure)
-- Probe 006: NEWID()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_006_newid`()
BEGIN
  SELECT
    GENERATE_UUID() AS g;
END;
