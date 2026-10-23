-- Source: dbo.probe_050_eomonth (SQL Server stored procedure)
-- Probe 050: EOMONTH() / DATEFROMPARTS()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_050_eomonth`()
BEGIN
  SELECT
    LAST_DAY(CAST(CURRENT_DATETIME() AS DATE)) AS a,
    DATE(2026, 1, 15) AS b;
END;
