-- Source: dbo.probe_044_concat_isnull (SQL Server stored procedure)
-- Probe 044: 'a' + ISNULL(x,'')
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_044_concat_isnull`()
BEGIN
  SELECT
    'a' || COALESCE(Name, '') AS s
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
