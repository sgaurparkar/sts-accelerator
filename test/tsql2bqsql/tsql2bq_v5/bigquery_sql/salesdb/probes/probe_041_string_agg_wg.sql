-- Source: dbo.probe_041_string_agg_wg (SQL Server stored procedure)
-- Probe 041: STRING_AGG ... WITHIN GROUP (ORDER BY)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_041_string_agg_wg`()
BEGIN
  SELECT
    STRING_AGG(Name, ','
    ORDER BY
      Name) AS s
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;
