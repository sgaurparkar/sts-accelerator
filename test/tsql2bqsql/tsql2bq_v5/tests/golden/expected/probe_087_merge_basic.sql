-- Source: dbo.probe_087_merge_basic (SQL Server stored procedure)
-- Probe 087: MERGE (basic)
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_087_merge_basic`()
BEGIN
  MERGE INTO `my-project.sql_server_centralasia_salesdb.T` AS t
  USING `my-project.sql_server_centralasia_salesdb.T2` AS s
  ON t.Id = s.Id
  WHEN MATCHED THEN UPDATE SET
    Name = s.Name
  WHEN NOT MATCHED THEN INSERT (Id, Name) VALUES (s.Id, s.Name);
END;
