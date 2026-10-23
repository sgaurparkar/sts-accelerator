-- Source: dbo.probe_004_identity_column (SQL Server stored procedure)
-- Probe 004: IDENTITY(1,1) column
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_004_identity_column`()
BEGIN
  -- TODO(review): IDENTITY on column Id dropped; BigQuery has no identity columns
  CREATE OR REPLACE TEMP TABLE tmp_x (
    Id INT64,
    Name STRING(10)
  );
  INSERT INTO tmp_x (
    Name
  )
  VALUES
    ('a');
  SELECT
    *
  FROM tmp_x;
END;
