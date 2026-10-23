-- Source: dbo.probe_014_xact_state (SQL Server stored procedure)
-- Probe 014: XACT_STATE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_014_xact_state`()
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
    IF (CASE WHEN v_in_transaction THEN -1 ELSE 0 END) = -1 THEN
      ROLLBACK TRANSACTION;
      SET v_in_transaction = FALSE;
    END IF;
  END;
END;
