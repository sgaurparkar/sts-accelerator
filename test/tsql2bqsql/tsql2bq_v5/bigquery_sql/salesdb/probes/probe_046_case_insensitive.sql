-- Source: dbo.probe_046_case_insensitive (SQL Server stored procedure)
-- Probe 046: Text equality (case-insensitive in SQL Server)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_046_case_insensitive`()
BEGIN
  -- TODO(review): string column Name is compared with a text literal: SQL Server's default collation is case-insensitive but BigQuery is case-sensitive, so 'NEW' will not match 'new'. Confirm the data's casing is consistent, or set config 'case_sensitivity': 'lower' to wrap both sides in LOWER()
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    Name = 'abc';
END;
