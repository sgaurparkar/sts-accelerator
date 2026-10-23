-- Source: dbo.probe_017_nested_tran (SQL Server stored procedure)
-- Probe 017: Nested BEGIN TRAN
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_017_nested_tran`()
BEGIN
  BEGIN TRANSACTION;
  -- NOTE: nested BEGIN TRANSACTION removed: T-SQL only increments @@TRANCOUNT, BigQuery does not allow nested transactions (the matching inner COMMIT is removed too)
  -- [removed] nested BEGIN TRANSACTION
  UPDATE `my-project.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    TRUE;
  -- [removed] inner COMMIT TRANSACTION (T-SQL only decrements @@TRANCOUNT)
  COMMIT TRANSACTION;
END;
