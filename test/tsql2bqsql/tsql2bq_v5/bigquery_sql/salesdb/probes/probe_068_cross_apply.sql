-- Source: dbo.probe_068_cross_apply (SQL Server stored procedure)
-- Probe 068: CROSS APPLY
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_068_cross_apply`()
BEGIN
  -- NOTE: CROSS APPLY -> CROSS JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) AS x (correlated; dry-run it - BigQuery rejects correlated subqueries it cannot de-correlate)
  SELECT
    c.Id,
    x.Total
  FROM `resource1.sql_server_centralasia_salesdb.T` AS c
  CROSS JOIN UNNEST(ARRAY(
    SELECT AS STRUCT
      SUM(Amount) AS Total
    FROM `resource1.sql_server_centralasia_salesdb.T2` AS o
    WHERE
      o.Id = c.Id
  )) AS x;
END;
