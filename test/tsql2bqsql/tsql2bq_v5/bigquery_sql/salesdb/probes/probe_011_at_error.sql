-- Source: dbo.probe_011_at_error (SQL Server stored procedure)
-- Probe 011: @@ERROR
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_011_at_error`()
BEGIN
  -- NOTE: IF @@ERROR check converted to an exception handler
  BEGIN
    UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
    WHERE
      Id = 1;
  EXCEPTION WHEN ERROR THEN
    SELECT
      1 AS failed;
  END;
END;
