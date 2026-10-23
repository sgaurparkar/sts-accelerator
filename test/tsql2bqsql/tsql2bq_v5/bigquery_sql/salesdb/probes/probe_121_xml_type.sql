-- Source: dbo.probe_121_xml_type (SQL Server stored procedure)
-- Probe 121: XML type
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_121_xml_type`()
BEGIN
  DECLARE v_x STRING;
  -- TODO(review): variable @x: XML mapped to STRING
  SET v_x = '<a/>';
  SELECT
    v_x AS x;
END;
