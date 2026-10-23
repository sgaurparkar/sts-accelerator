-- Source: dbo.probe_118_bit_compare (SQL Server stored procedure)
-- Probe 118: BIT column compared with 1
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_118_bit_compare`()
BEGIN
  SELECT
    Id
  FROM `my-project.sql_server_centralasia_salesdb.T`
  WHERE
    IsActive = TRUE;
END;
