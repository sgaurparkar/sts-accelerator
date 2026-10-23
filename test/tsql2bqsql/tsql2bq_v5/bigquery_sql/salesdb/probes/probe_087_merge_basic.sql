-- Source: dbo.probe_087_merge_basic (SQL Server stored procedure)
-- Probe 087: MERGE (basic)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_087_merge_basic`()
BEGIN
  MERGE INTO `resource1.sql_server_centralasia_salesdb.T` AS t
  USING `resource1.sql_server_centralasia_salesdb.T2` AS s
  ON t.Id = s.Id
  WHEN MATCHED THEN UPDATE SET
    Name = s.Name
  WHEN NOT MATCHED THEN INSERT (Id, Name) VALUES (s.Id, s.Name);
END;
