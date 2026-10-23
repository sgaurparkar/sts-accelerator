-- Source: dbo.probe_054_convert_style (SQL Server stored procedure)
-- Probe 054: CONVERT(VARCHAR, date, style)
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_054_convert_style`()
BEGIN
  SELECT
    SUBSTRING(FORMAT_DATETIME('%Y-%m-%d %H:%M:%S', CAST(CreatedAt AS DATETIME)), 1, 10) AS a,
    SUBSTRING(FORMAT_DATETIME('%d/%m/%Y', CAST(CreatedAt AS DATETIME)), 1, 10) AS b
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
