-- Source: dbo.probe_116_sp_rename (SQL Server stored procedure)
-- Probe 116: System procedure (sp_rename)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_116_sp_rename`()
BEGIN
  -- NOTE: sp_rename (COLUMN) -> ALTER TABLE ... RENAME COLUMN (DDL: not allowed inside a transaction)
  ALTER TABLE `resource1.sql_server_centralasia_salesdb.T` RENAME COLUMN Name TO Name2;
END;
