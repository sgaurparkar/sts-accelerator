-- Source: dbo.probe_072_unpivot (SQL Server stored procedure)
-- Probe 072: UNPIVOT
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_072_unpivot`()
BEGIN
  -- NOTE: UNPIVOT: converted to BigQuery UNPIVOT syntax; compare the result columns once
  SELECT
    *
  FROM `my-project.sql_server_centralasia_salesdb.T`
  UNPIVOT(Val FOR Col IN (A, B)) AS u;
END;
