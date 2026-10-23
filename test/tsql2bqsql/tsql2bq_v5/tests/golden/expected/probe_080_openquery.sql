-- Source: dbo.probe_080_openquery (SQL Server stored procedure)
-- Probe 080: OPENQUERY / linked server
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_080_openquery`()
BEGIN
  -- TODO(review): OPENQUERY: linked servers not supported
  SELECT
    *
  FROM OPENQUERY(LinkedSrv, 'SELECT 1 AS a');
END;
