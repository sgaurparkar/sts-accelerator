-- Source: dbo.probe_113_multi_resultset (SQL Server stored procedure)
-- Probe 113: Several SELECT result sets
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_113_multi_resultset`()
BEGIN
  SELECT
    1 AS a;
  SELECT
    2 AS b;
END;
