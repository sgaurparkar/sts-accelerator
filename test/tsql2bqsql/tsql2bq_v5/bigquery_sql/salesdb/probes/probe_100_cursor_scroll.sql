-- Source: dbo.probe_100_cursor_scroll (SQL Server stored procedure)
-- Probe 100: CURSOR SCROLL (FETCH PRIOR / LAST)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_100_cursor_scroll`()
BEGIN
  DECLARE v_i INT64;
  -- TODO(review): cursors are not supported; rewrite as FOR record IN (SELECT ...) DO ... END FOR
  -- TODO(manual): cursor declaration
  --   DECLARE c CURSOR SCROLL FOR SELECT Id FROM dbo.T
  -- TODO(review): OPEN is not supported; rewrite manually
  -- TODO(manual): OPEN is not supported in BigQuery
  --   OPEN c
  -- TODO(review): FETCH is not supported; rewrite manually
  -- TODO(manual): FETCH is not supported in BigQuery
  --   FETCH LAST FROM c INTO @i
  -- TODO(review): FETCH is not supported; rewrite manually
  -- TODO(manual): FETCH is not supported in BigQuery
  --   FETCH PRIOR FROM c INTO @i
  -- TODO(review): CLOSE is not supported; rewrite manually
  -- TODO(manual): CLOSE is not supported in BigQuery
  --   CLOSE c
  -- TODO(review): DEALLOCATE is not supported; rewrite manually
  -- TODO(manual): DEALLOCATE is not supported in BigQuery
  --   DEALLOCATE c
END;
