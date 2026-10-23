-- Source: dbo.probe_069_outer_apply (SQL Server stored procedure)
-- Probe 069: OUTER APPLY
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_069_outer_apply`()
BEGIN
  -- NOTE: OUTER APPLY -> LEFT JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) AS x (correlated; dry-run it - BigQuery rejects correlated subqueries it cannot de-correlate)
  SELECT
    c.Id,
    x.Total
  FROM `my-project.sql_server_centralasia_salesdb.T` AS c
  LEFT JOIN UNNEST(ARRAY(
    SELECT AS STRUCT
      SUM(Amount) AS Total
    FROM `my-project.sql_server_centralasia_salesdb.T2` AS o
    WHERE
      o.Id = c.Id
  )) AS x
    ON TRUE;
END;
