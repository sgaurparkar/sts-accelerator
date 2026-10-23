-- Source: dbo.probe_042_char_unicode (SQL Server stored procedure)
-- Probe 042: CHAR() / NCHAR() / UNICODE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_042_char_unicode`()
BEGIN
  SELECT
    CHR(  65) AS c,
    UNICODE('A') AS u,
    CODE_POINTS_TO_STRING([66]) AS n;
END;
