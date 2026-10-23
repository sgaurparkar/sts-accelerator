-- Source: dbo.probe_099_cursor_basic (SQL Server stored procedure)
-- Probe 099: CURSOR (forward-only) + @@FETCH_STATUS
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_099_cursor_basic`()
BEGIN
  DECLARE v_i INT64;
  -- [cursor -> FOR loop] DECLARE c CURSOR FOR SELECT Id FROM dbo.T
  -- [cursor -> FOR loop] OPEN c
  -- [cursor -> FOR loop] FETCH NEXT FROM c INTO @i
  -- NOTE: cursor c converted to FOR loop (BigQuery has no cursors)
  FOR rec_c IN (
    SELECT
      Id
    FROM `my-project.sql_server_centralasia_salesdb.T`
  ) DO
    SET v_i = rec_c.Id;
    UPDATE `my-project.sql_server_centralasia_salesdb.T` SET Name = 'x'
    WHERE
      Id = v_i;
  END FOR;
  -- [cursor -> FOR loop] CLOSE c
  -- [cursor -> FOR loop] DEALLOCATE c
END;
