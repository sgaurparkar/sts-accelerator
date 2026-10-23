-- Source: dbo.probe_052_sysdatetime (SQL Server stored procedure)
-- Probe 052: SYSDATETIME() / GETUTCDATE() / SYSDATETIMEOFFSET()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_052_sysdatetime`()
BEGIN
  SELECT
    CURRENT_DATETIME() AS a,
    CURRENT_DATETIME('UTC') AS b,
    CURRENT_TIMESTAMP() AS c;
END;
