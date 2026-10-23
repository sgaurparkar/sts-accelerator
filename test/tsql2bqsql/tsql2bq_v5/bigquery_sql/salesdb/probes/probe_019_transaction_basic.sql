-- Source: dbo.probe_019_transaction_basic (SQL Server stored procedure)
-- Probe 019: BEGIN/COMMIT TRANSACTION
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_019_transaction_basic`()
BEGIN
  BEGIN TRANSACTION;
  UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    Id = 1;
  COMMIT TRANSACTION;
END;
