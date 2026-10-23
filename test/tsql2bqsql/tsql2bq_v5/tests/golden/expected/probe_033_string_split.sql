-- Source: dbo.probe_033_string_split (SQL Server stored procedure)
-- Probe 033: STRING_SPLIT()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_033_string_split`()
BEGIN
  -- NOTE: STRING_SPLIT(s, sep) -> UNNEST(SPLIT(s, sep)) AS value (same rows; the order of the values is not guaranteed in either engine unless enable_ordinal / WITH OFFSET is used)
  SELECT
    value
  FROM (
    SELECT
      value
    FROM UNNEST(SPLIT('a,b,c', ',')) AS value
  ) AS value_list;
END;
