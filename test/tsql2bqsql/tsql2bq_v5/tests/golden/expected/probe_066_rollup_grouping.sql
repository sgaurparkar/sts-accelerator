-- Source: dbo.probe_066_rollup_grouping (SQL Server stored procedure)
-- Probe 066: GROUP BY ROLLUP + GROUPING_ID()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_066_rollup_grouping`()
BEGIN
  SELECT
    Name,
    SUM(Amount) AS s,
    GROUPING(Name) AS g
  FROM `my-project.sql_server_centralasia_salesdb.T`
  GROUP BY
    ROLLUP (
      Name
    );
END;
