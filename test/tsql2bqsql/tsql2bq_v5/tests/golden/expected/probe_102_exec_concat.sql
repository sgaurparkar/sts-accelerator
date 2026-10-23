-- Source: dbo.probe_102_exec_concat (SQL Server stored procedure)
-- Probe 102: EXEC(@sql) with concatenated string
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_102_exec_concat`()
BEGIN
  DECLARE v_n STRING;
  DECLARE v_sql STRING;
  SET v_n = 'T';
  SET v_sql = 'SELECT * FROM dbo.' || v_n;
  -- TODO(review): dynamic SQL: the string being executed is still T-SQL and must be converted too
  -- TODO(review): SECURITY: dynamic SQL is built by string concatenation (@sql). If any part comes from a parameter or table data this is an SQL injection risk: pass values with EXECUTE IMMEDIATE ... USING, and allow-list identifiers (table / column names) instead of concatenating them
  EXECUTE IMMEDIATE v_sql;
END;
