-- Source: dbo.probe_059_hashbytes (SQL Server stored procedure)
-- Probe 059: HASHBYTES()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_059_hashbytes`()
BEGIN
  -- TODO(review): HASHBYTES: use MD5()/SHA256()/SHA512()
  SELECT
    SHA256(Name) AS h
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
