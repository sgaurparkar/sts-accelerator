-- Source: dbo.probe_078_openjson (SQL Server stored procedure)
-- Probe 078: OPENJSON()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_078_openjson`()
BEGIN
  -- TODO(review): OPENJSON: use JSON functions / UNNEST(JSON_QUERY_ARRAY(...))
  SELECT
    `key`,
    value
  FROM OPENJSON('{"a":1}');
END;
