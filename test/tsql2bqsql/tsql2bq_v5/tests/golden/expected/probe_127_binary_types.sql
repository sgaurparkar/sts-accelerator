-- Source: dbo.probe_127_binary_types (SQL Server stored procedure)
-- Probe 127: VARBINARY(MAX) / 0x literal
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_127_binary_types`()
BEGIN
  DECLARE v_b BYTES;
  SET v_b = FROM_HEX('0102');
  SELECT
    v_b AS b;
END;
