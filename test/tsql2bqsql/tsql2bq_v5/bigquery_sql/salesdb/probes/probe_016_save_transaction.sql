-- Source: dbo.probe_016_save_transaction (SQL Server stored procedure)
-- Probe 016: SAVE TRANSACTION (savepoint)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_016_save_transaction`()
BEGIN
  BEGIN TRANSACTION;
  -- TODO(review): savepoints are not supported in BigQuery
  -- TODO(manual): savepoints are not supported
  --   SAVE TRANSACTION sp1
  UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    TRUE;
  -- TODO(review): ROLLBACK to a savepoint: BigQuery has no savepoints - this would roll back the WHOLE transaction
  -- TODO(manual): rollback to savepoint is not supported
  --   ROLLBACK TRANSACTION sp1
  COMMIT TRANSACTION;
END;
