-- Source: dbo.probe_079_json_value (SQL Server stored procedure)
-- Probe 079: JSON_VALUE()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_079_json_value`()
BEGIN
  SELECT
    JSON_EXTRACT_SCALAR('{"a":1}', '$.a') AS v;
END;
