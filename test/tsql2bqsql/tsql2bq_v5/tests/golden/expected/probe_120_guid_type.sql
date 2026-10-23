-- Source: dbo.probe_120_guid_type (SQL Server stored procedure)
-- Probe 120: UNIQUEIDENTIFIER
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_120_guid_type`()
BEGIN
  DECLARE v_g STRING;
  SET v_g = GENERATE_UUID();
  SELECT
    v_g AS g;
END;
