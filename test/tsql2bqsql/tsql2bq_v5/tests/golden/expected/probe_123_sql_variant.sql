-- Source: dbo.probe_123_sql_variant (SQL Server stored procedure)
-- Probe 123: SQL_VARIANT
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_123_sql_variant`()
BEGIN
  DECLARE v_v STRING;
  -- TODO(review): variable @v: user-defined or unsupported type 'SQL_VARIANT' mapped to STRING
  SET v_v = 1;
  SELECT
    v_v AS v;
END;
