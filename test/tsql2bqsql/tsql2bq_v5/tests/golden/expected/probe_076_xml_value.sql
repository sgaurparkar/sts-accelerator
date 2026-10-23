-- Source: dbo.probe_076_xml_value (SQL Server stored procedure)
-- Probe 076: XML .value() / .query() / .nodes()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_076_xml_value`()
BEGIN
  DECLARE v_x STRING;
  -- TODO(review): variable @x: XML mapped to STRING
  SET v_x = '<a><b>1</b></a>';
  SELECT
    v_x.value('(/a/b)[1]', 'INT') AS v;
END;
