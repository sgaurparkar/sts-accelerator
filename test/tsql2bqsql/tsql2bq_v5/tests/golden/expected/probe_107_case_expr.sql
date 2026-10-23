-- Source: dbo.probe_107_case_expr (SQL Server stored procedure)
-- Probe 107: CASE expression
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_107_case_expr`()
BEGIN
  SELECT
    CASE WHEN Amount > 1 THEN 'h' ELSE 'l' END AS c
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
