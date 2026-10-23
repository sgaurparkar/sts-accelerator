-- Source: dbo.probe_001_scope_identity (SQL Server stored procedure)
-- Probe 001: SCOPE_IDENTITY()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_001_scope_identity`()
BEGIN
  DECLARE v_id INT64;
  INSERT INTO `resource1.sql_server_centralasia_salesdb.T` (
    Name
  )
  VALUES
    ('a');
  -- TODO(review): SCOPE_IDENTITY: no identity columns in BigQuery; generate keys explicitly (e.g. GENERATE_UUID())
  SET v_id = SCOPE_IDENTITY();
  SELECT
    v_id AS NewId;
END;
