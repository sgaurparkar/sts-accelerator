-- Source: dbo.probe_091_fulltext (SQL Server stored procedure)
-- Probe 091: Full-text CONTAINS()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_091_fulltext`()
BEGIN
  -- NOTE: full-text CONTAINS -> CONTAINS_SUBSTR (case-insensitive SUBSTRING match, not word match: 'cat' also matches 'concatenate'); consider SEARCH()
  SELECT
    Id
  FROM `my-project.sql_server_centralasia_salesdb.T`
  WHERE
    CONTAINS_SUBSTR(Name, 'abc');
END;
