-- Source: dbo.probe_064_stdev_var (SQL Server stored procedure)
-- Probe 064: STDEV() / VAR()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_064_stdev_var`()
BEGIN
  SELECT
    STDDEV(Amount) AS s,
    VAR_SAMP(Amount) AS v
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
