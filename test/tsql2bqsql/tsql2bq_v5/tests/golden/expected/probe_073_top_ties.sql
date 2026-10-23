-- Source: dbo.probe_073_top_ties (SQL Server stored procedure)
-- Probe 073: TOP n WITH TIES
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_073_top_ties`()
BEGIN
  -- NOTE: TOP n WITH TIES converted to QUALIFY RANK() OVER (ORDER BY ...) <= n
  SELECT
    Id,
    Amount
  FROM `my-project.sql_server_centralasia_salesdb.T`
  WHERE
    TRUE
  QUALIFY
    RANK() OVER (ORDER BY Amount DESC) <= 3
  ORDER BY
    Amount DESC;
END;
