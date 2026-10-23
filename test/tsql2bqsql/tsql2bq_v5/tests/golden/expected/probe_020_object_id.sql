-- Source: dbo.probe_020_object_id (SQL Server stored procedure)
-- Probe 020: OBJECT_ID()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_020_object_id`()
BEGIN
  -- NOTE: OBJECT_ID('dbo.T') IS [NOT] NULL -> [NOT] EXISTS on sql_server_centralasia_salesdb.INFORMATION_SCHEMA.TABLES
  IF EXISTS(SELECT 1 FROM `my-project.sql_server_centralasia_salesdb.INFORMATION_SCHEMA.TABLES` WHERE table_name = 'T') THEN
    SELECT
      1 AS e;
  END IF;
END;
