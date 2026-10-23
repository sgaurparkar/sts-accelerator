-- Source: dbo.probe_119_money (SQL Server stored procedure)
-- Probe 119: MONEY / SMALLMONEY
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_119_money`()
BEGIN
  DECLARE v_m NUMERIC;
  DECLARE v_s NUMERIC;
  SET v_m = 1.2345;
  SET v_s = 2;
  SELECT
    v_m + v_s AS t;
END;
