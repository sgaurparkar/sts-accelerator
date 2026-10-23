-- Source: dbo.probe_038_datalength (SQL Server stored procedure)
-- Probe 038: DATALENGTH()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_038_datalength`()
BEGIN
  -- NOTE: DATALENGTH -> BYTE_LENGTH (T-SQL counts 2 bytes per NVARCHAR character, BigQuery counts UTF-8 bytes)
  SELECT
    BYTE_LENGTH(Name) AS d
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
