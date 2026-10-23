-- Source: dbo.probe_028_stuff (SQL Server stored procedure)
-- Probe 028: STUFF()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_028_stuff`()
BEGIN
  -- NOTE: STUFF(s, start, len, rep) -> SUBSTR(s, 1, start-1) || rep || SUBSTR(s, start+len) (T-SQL returns NULL when start is 0 or beyond the string; this version does not)
  SELECT
    (
      SUBSTRING('abcdef', 1, 2 - 1) || 'X' || SUBSTRING('abcdef', 2 + 3)
    ) AS s;
END;
