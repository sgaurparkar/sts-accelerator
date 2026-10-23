-- Source: dbo.probe_043_collate (SQL Server stored procedure)
-- Probe 043: COLLATE (case-sensitive collation)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_043_collate`()
BEGIN
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    Name = 'a';
END;
