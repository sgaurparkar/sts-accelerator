-- Source: dbo.probe_049_dateadd_diff (SQL Server stored procedure)
-- Probe 049: DATEADD() / DATEDIFF()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_049_dateadd_diff`()
BEGIN
  -- NOTE: DATEADD on an expression of unknown type: DATE_ADD only accepts DATE - if it is DATETIME/TIMESTAMP use DATETIME_ADD/TIMESTAMP_ADD (or list the column in config 'column_types')
  SELECT
    DATE_ADD(CreatedAt, INTERVAL 5 DAY) AS a,
    DATETIME_DIFF(CURRENT_DATETIME(), CAST(CreatedAt AS DATETIME), DAY) AS b
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
