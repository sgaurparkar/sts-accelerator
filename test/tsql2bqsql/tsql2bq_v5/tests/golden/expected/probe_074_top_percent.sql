-- Source: dbo.probe_074_top_percent (SQL Server stored procedure)
-- Probe 074: TOP n PERCENT
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_074_top_percent`()
BEGIN
  -- NOTE: TOP n PERCENT converted to QUALIFY ROW_NUMBER() OVER (ORDER BY ...) <= CEIL(COUNT(*) OVER () * p / 100)
  SELECT
    Id
  FROM `my-project.sql_server_centralasia_salesdb.T`
  WHERE
    TRUE
  QUALIFY
    ROW_NUMBER() OVER (ORDER BY Id) <= CEIL(COUNT(*) OVER () * 10 / 100.0)
  ORDER BY
    Id;
END;
