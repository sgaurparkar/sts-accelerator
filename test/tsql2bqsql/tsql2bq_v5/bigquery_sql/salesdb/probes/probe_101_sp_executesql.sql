-- Source: dbo.probe_101_sp_executesql (SQL Server stored procedure)
-- Probe 101: sp_executesql with parameters
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_101_sp_executesql`()
BEGIN
  -- TODO(review): sp_executesql converted to EXECUTE IMMEDIATE ... USING; check the result
  EXECUTE IMMEDIATE r"""SELECT
  *
FROM `resource1.sql_server_centralasia_salesdb.T`
WHERE
  Id = @id""" USING 1 AS id;
END;
