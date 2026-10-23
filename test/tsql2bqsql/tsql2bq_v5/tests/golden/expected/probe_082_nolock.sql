-- Source: dbo.probe_082_nolock (SQL Server stored procedure)
-- Probe 082: WITH (NOLOCK) table hint
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_082_nolock`()
BEGIN
  SELECT
    Id
  FROM `my-project.sql_server_centralasia_salesdb.T`
  WHERE
    Id = 1;
END;
