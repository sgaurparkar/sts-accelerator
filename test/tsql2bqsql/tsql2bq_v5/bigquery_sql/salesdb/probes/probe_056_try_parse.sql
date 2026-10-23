-- Source: dbo.probe_056_try_parse (SQL Server stored procedure)
-- Probe 056: TRY_PARSE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_056_try_parse`()
BEGIN
  -- TODO(review): TRY_PARSE(x AS DATE) -> SAFE_CAST: BigQuery only parses ISO formats (YYYY-MM-DD, plain numbers); T-SQL also accepts culture formats ('12/31/2020', '$1,000') - use SAFE.PARSE_DATE('%m/%d/%Y', x) for those
  SELECT
    SAFE_CAST(Name AS DATE) AS a
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
