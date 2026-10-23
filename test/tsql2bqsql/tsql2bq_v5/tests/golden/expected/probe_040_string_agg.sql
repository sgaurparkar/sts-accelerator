-- Source: dbo.probe_040_string_agg (SQL Server stored procedure)
-- Probe 040: STRING_AGG()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_040_string_agg`()
BEGIN
  SELECT
    STRING_AGG(Name, ',') AS s
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
