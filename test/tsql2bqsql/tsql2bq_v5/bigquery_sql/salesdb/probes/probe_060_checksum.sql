-- Source: dbo.probe_060_checksum (SQL Server stored procedure)
-- Probe 060: CHECKSUM() / BINARY_CHECKSUM()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_060_checksum`()
BEGIN
  -- TODO(review): CHECKSUM: use FARM_FINGERPRINT()
  -- TODO(review): BINARY_CHECKSUM: use FARM_FINGERPRINT()
  SELECT
    CHECKSUM(Name) AS c,
    BINARY_CHECKSUM(Name) AS b
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
