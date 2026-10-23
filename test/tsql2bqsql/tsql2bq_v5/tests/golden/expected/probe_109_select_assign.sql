-- Source: dbo.probe_109_select_assign (SQL Server stored procedure)
-- Probe 109: SELECT @var = col FROM ...
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_109_select_assign`()
BEGIN
  DECLARE v_n STRING;
  -- NOTE: SELECT @var = ... FROM ... assigns the LAST row in T-SQL; BigQuery version uses LIMIT 1 (add ORDER BY if the row matters). If NO row matches, T-SQL keeps the old value but BigQuery sets the variable to NULL
  SET v_n = (
    SELECT
      Name
    FROM `my-project.sql_server_centralasia_salesdb.T`
    WHERE
      Id = 1
    LIMIT 1
  );
  SELECT
    v_n AS n;
END;
