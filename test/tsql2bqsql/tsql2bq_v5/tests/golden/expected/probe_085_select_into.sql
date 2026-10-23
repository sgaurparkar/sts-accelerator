-- Source: dbo.probe_085_select_into (SQL Server stored procedure)
-- Probe 085: SELECT ... INTO #t
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_085_select_into`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_copy AS SELECT
    Id,
    Name
  FROM `my-project.sql_server_centralasia_salesdb.T`;
  SELECT
    *
  FROM tmp_copy;
END;
