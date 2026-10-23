-- Source: dbo.probe_093_global_temp (SQL Server stored procedure)
-- Probe 093: ##global temp table
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_093_global_temp`()
BEGIN
  -- NOTE: global temp table ##g becomes a script-local TEMP TABLE: other sessions cannot see it in BigQuery
  CREATE OR REPLACE TEMP TABLE tmp_g (
    Id INT64
  );
  -- NOTE: global temp table ##g becomes a script-local TEMP TABLE: other sessions cannot see it in BigQuery
  INSERT INTO tmp_g
  VALUES
    (1);
  -- NOTE: global temp table ##g becomes a script-local TEMP TABLE: other sessions cannot see it in BigQuery
  SELECT
    *
  FROM tmp_g;
END;
