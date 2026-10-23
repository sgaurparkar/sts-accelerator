-- Source: dbo.probe_126_datetime_types (SQL Server stored procedure)
-- Probe 126: DATETIME2 / SMALLDATETIME / DATETIMEOFFSET
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_126_datetime_types`()
BEGIN
  DECLARE v_a DATETIME;
  DECLARE v_b DATETIME;
  DECLARE v_c TIMESTAMP;
  SET v_a = CURRENT_DATETIME();
  SET v_b = CURRENT_DATETIME();
  SET v_c = CURRENT_TIMESTAMP();
  SELECT
    v_a AS a,
    v_b AS b,
    v_c AS c;
END;
