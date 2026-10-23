-- Source: dbo.probe_010_error_message (SQL Server stored procedure)
-- Probe 010: ERROR_MESSAGE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_010_error_message`()
BEGIN
  BEGIN
    SELECT
      DIV(1, 0);
  EXCEPTION WHEN ERROR THEN
    SELECT
      @@error.message AS m;
  END;
END;
