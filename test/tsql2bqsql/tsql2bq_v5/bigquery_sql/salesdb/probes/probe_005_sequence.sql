-- Source: dbo.probe_005_sequence (SQL Server stored procedure)
-- Probe 005: NEXT VALUE FOR (SEQUENCE)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_005_sequence`()
BEGIN
  -- TODO(review): NEXT VALUE FOR <sequence>: BigQuery has no sequences; use GENERATE_UUID() or ROW_NUMBER() + MAX(existing key)
  SELECT
    NEXT VALUE FOR dbo.Seq1 AS n;
END;
