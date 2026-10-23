-- Source: dbo.probe_088_merge_output (SQL Server stored procedure)
-- Probe 088: MERGE ... OUTPUT $action
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_088_merge_output`()
BEGIN
  -- TODO(review): OUTPUT: OUTPUT clause is not supported
  MERGE INTO `my-project.sql_server_centralasia_salesdb.T` AS t
  USING (
    SELECT
      1 AS Id
  ) AS s
  ON t.Id = s.Id
  WHEN MATCHED THEN UPDATE SET
    Name = 'x'
  RETURNING $action, inserted.Id;
END;
