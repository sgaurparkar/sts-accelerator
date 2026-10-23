-- Source: dbo.probe_025_rowcount (SQL Server stored procedure)
-- Probe 025: @@ROWCOUNT
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_025_rowcount`()
BEGIN
  UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    TRUE;
  SELECT
    @@row_count AS n;
END;
