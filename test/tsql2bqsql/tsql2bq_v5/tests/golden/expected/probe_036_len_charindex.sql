-- Source: dbo.probe_036_len_charindex (SQL Server stored procedure)
-- Probe 036: LEN() / CHARINDEX()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_036_len_charindex`()
BEGIN
  -- NOTE: CHARINDEX/INSTR: SQL Server's default collation finds 'A' in 'abc', BigQuery INSTR is case-sensitive (case_sensitivity 'lower' wraps both sides in LOWER())
  SELECT
    LENGTH(RTRIM(CAST(Name AS STRING), ' ')) AS l,
    INSTR(Name, 'a') AS c
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
