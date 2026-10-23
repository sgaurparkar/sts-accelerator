-- Source: dbo.probe_034_difference (SQL Server stored procedure)
-- Probe 034: DIFFERENCE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_034_difference`()
BEGIN
  -- TODO(review): DIFFERENCE(a, b) -> position-by-position SOUNDEX comparison (0-4); T-SQL's algorithm can score differently for some inputs
  SELECT
    (
      SELECT
        COUNTIF(SUBSTR(SOUNDEX('a'), i, 1) = SUBSTR(SOUNDEX('b'), i, 1))
      FROM UNNEST([1, 2, 3, 4]) AS i
    ) AS d;
END;
