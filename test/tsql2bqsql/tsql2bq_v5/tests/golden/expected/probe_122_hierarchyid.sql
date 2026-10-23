-- Source: dbo.probe_122_hierarchyid (SQL Server stored procedure)
-- Probe 122: HIERARCHYID
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_122_hierarchyid`()
BEGIN
  DECLARE v_h STRING;
  -- TODO(review): variable @h: user-defined or unsupported type 'HIERARCHYID' mapped to STRING
  SELECT
    v_h AS h;
END;
