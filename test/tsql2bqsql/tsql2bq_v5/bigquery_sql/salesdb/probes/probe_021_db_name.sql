-- Source: dbo.probe_021_db_name (SQL Server stored procedure)
-- Probe 021: DB_NAME()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_021_db_name`()
BEGIN
  -- NOTE: DB_NAME() replaced by the target dataset name 'sql_server_centralasia_salesdb'
  SELECT
    'sql_server_centralasia_salesdb' AS d;
END;
