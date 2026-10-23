-- Source: dbo.probe_035_like_class (SQL Server stored procedure)
-- Probe 035: LIKE '[a-c]%' character class
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_035_like_class`()
BEGIN
  -- NOTE: LIKE '[a-c]%' uses T-SQL character classes, converted to REGEXP_CONTAINS
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    REGEXP_CONTAINS(Name, '(?s)^[a-c].*$');
END;
