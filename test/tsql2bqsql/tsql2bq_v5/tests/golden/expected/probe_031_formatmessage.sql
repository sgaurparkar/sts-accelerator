-- Source: dbo.probe_031_formatmessage (SQL Server stored procedure)
-- Probe 031: FORMATMESSAGE()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_031_formatmessage`()
BEGIN
  -- NOTE: FORMATMESSAGE -> FORMAT (printf-style; %i/%u became %d)
  SELECT
    FORMAT('x %d', 5) AS m;
END;
