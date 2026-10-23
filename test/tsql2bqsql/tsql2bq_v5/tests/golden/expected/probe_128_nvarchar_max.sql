-- Source: dbo.probe_128_nvarchar_max (SQL Server stored procedure)
-- Probe 128: NVARCHAR(MAX)
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_128_nvarchar_max`()
BEGIN
  DECLARE v_s STRING;
  SET v_s = 'x';
  SELECT
    v_s AS s;
END;
