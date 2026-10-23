-- Source: dbo.probe_053_isdate (SQL Server stored procedure)
-- Probe 053: ISDATE()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_053_isdate`()
BEGIN
  -- TODO(review): ISDATE(x) -> IF(SAFE_CAST(x AS DATETIME/DATE) IS NOT NULL, 1, 0): only ISO strings count as dates in BigQuery ('Jan 1 2020', '01/02/2020' return 0 here, 1 in T-SQL)
  SELECT
    IF(NOT SAFE_CAST(Name AS DATETIME) IS NULL OR NOT SAFE_CAST(Name AS DATE) IS NULL, 1, 0) AS d
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
