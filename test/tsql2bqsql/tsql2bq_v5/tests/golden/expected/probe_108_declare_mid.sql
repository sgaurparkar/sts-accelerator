-- Source: dbo.probe_108_declare_mid (SQL Server stored procedure)
-- Probe 108: DECLARE in the middle of the block
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_108_declare_mid`()
BEGIN
  DECLARE v_x INT64;
  SELECT
    1 AS a;
  SET v_x = 5;
  SELECT
    v_x AS x;
END;
