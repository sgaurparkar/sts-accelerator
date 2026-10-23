-- Source: dbo.probe_025_rowcount (SQL Server stored procedure)
-- Probe 025: @@ROWCOUNT
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_025_rowcount`()
BEGIN
  UPDATE `my-project.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    TRUE;
  SELECT
    @@row_count AS n;
END;
