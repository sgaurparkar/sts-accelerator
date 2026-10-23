-- Source: dbo.probe_015_trancount (SQL Server stored procedure)
-- Probe 015: @@TRANCOUNT guard
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_015_trancount`()
BEGIN
  DECLARE v_in_transaction BOOL DEFAULT FALSE;
  BEGIN
    BEGIN TRANSACTION;
    SET v_in_transaction = TRUE;
    UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
    WHERE
      TRUE;
    COMMIT TRANSACTION;
    SET v_in_transaction = FALSE;
  EXCEPTION WHEN ERROR THEN
    IF v_in_transaction THEN
      ROLLBACK TRANSACTION;
      SET v_in_transaction = FALSE;
    END IF;
  END;
END;
