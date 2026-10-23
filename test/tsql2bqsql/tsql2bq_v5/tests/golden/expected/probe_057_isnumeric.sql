-- Source: dbo.probe_057_isnumeric (SQL Server stored procedure)
-- Probe 057: ISNUMERIC()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_057_isnumeric`()
BEGIN
  -- TODO(review): ISNUMERIC(x) -> IF(SAFE_CAST(x AS FLOAT64) IS NOT NULL, 1, 0): T-SQL also returns 1 for '$', ',', '.', '-', '1e5d' ... and 0 for 'NaN'/'inf'; check the inputs you expect
  SELECT
    IF(NOT SAFE_CAST(Name AS FLOAT64) IS NULL, 1, 0) AS n
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
