-- Source: dbo.probe_047_year_month_day (SQL Server stored procedure)
-- Probe 047: YEAR() / MONTH() / DAY()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_047_year_month_day`()
BEGIN
  SELECT
    EXTRACT(YEAR FROM CreatedAt) AS y,
    EXTRACT(MONTH FROM CreatedAt) AS m,
    EXTRACT(DAY FROM CreatedAt) AS d
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
