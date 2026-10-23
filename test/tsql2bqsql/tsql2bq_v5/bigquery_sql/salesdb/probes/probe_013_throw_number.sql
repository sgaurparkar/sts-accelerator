-- Source: dbo.probe_013_throw_number (SQL Server stored procedure)
-- Probe 013: THROW <number>, <msg>, <state>
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_013_throw_number`()
BEGIN
  RAISE USING MESSAGE = 'custom error';
END;
