-- Source: dbo.probe_125_geography (SQL Server stored procedure)
-- Probe 125: GEOGRAPHY::Point / STAsText
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_125_geography`()
BEGIN
  DECLARE v_g GEOGRAPHY;
  SET v_g = ST_GEOGPOINT(2, 1);
  SELECT
    ST_ASTEXT(v_g) AS t;
END;
