-- Source: dbo.probe_002_at_identity (SQL Server stored procedure)
-- Probe 002: @@IDENTITY
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_002_at_identity`()
BEGIN
  DECLARE v_id INT64;
  -- TODO(review): @@IDENTITY: no identity columns in BigQuery
  SET v_id = @@IDENTITY;
  SELECT
    v_id AS i;
END;
