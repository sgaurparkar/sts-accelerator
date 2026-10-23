-- Source: dbo.probe_003_ident_current (SQL Server stored procedure)
-- Probe 003: IDENT_CURRENT()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_003_ident_current`()
BEGIN
  -- TODO(review): IDENT_CURRENT: no identity columns in BigQuery
  SELECT
    IDENT_CURRENT('dbo.T') AS i;
END;
