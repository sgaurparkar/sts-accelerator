-- Source: dbo.probe_061_rand_seed (SQL Server stored procedure)
-- Probe 061: RAND(seed)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_061_rand_seed`()
BEGIN
  -- TODO(review): RAND(seed): BigQuery RAND() takes no seed, so the value is not repeatable
  SELECT
    RAND() AS r;
END;
