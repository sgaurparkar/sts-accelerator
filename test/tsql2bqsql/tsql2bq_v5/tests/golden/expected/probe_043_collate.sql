-- Source: dbo.probe_043_collate (SQL Server stored procedure)
-- Probe 043: COLLATE (case-sensitive collation)
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_043_collate`()
BEGIN
  SELECT
    Id
  FROM `my-project.sql_server_centralasia_salesdb.T`
  WHERE
    Name = 'a';
END;
