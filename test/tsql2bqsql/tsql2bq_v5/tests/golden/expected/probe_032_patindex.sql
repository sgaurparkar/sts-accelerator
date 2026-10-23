-- Source: dbo.probe_032_patindex (SQL Server stored procedure)
-- Probe 032: PATINDEX()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_032_patindex`()
BEGIN
  -- NOTE: PATINDEX('%b%', x) -> REGEXP_INSTR(x, '(?s)b') (case-sensitive; SQL Server's default collation is not - set case_sensitivity 'lower' to add (?i))
  SELECT
    REGEXP_INSTR(Name, '(?s)b') AS p
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
