-- Source: dbo.probe_129_temp_table (SQL Server stored procedure)
-- Probe 129: #temp table
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_129_temp_table`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_t (
    Id INT64,
    Name STRING(10)
  );
  INSERT INTO tmp_t
  VALUES
    (1, 'a');
  SELECT
    *
  FROM tmp_t;
  DROP TABLE IF EXISTS tmp_t;
END;
