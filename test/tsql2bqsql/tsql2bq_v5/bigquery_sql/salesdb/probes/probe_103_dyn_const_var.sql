-- Source: dbo.probe_103_dyn_const_var (SQL Server stored procedure)
-- Probe 103: Constant dynamic SQL stored in a variable
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_103_dyn_const_var`()
BEGIN
  DECLARE v_sql STRING;
  SET v_sql = r"""UPDATE `resource1.sql_server_centralasia_salesdb.T` SET LastRun = CURRENT_DATETIME()
WHERE
  Id = @id""";
  -- TODO(review): sp_executesql converted to EXECUTE IMMEDIATE ... USING; check the result
  EXECUTE IMMEDIATE v_sql USING 1 AS id;
END;
