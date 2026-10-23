-- Source: dbo.probe_062_int_division (SQL Server stored procedure)
-- Probe 062: int / int
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_062_int_division`()
BEGIN
  DECLARE v_a INT64;
  DECLARE v_b INT64;
  SET v_a = 5;
  SET v_b = 2;
  SELECT
    DIV(v_a, v_b) AS q;
END;
