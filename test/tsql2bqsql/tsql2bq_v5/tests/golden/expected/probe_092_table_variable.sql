-- Source: dbo.probe_092_table_variable (SQL Server stored procedure)
-- Probe 092: DECLARE @t TABLE
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_092_table_variable`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_t (
    Id INT64,
    Name STRING(10)
  );
  INSERT INTO tmp_t
  SELECT
    Id,
    Name
  FROM `my-project.sql_server_centralasia_salesdb.T`;
  SELECT
    *
  FROM tmp_t;
END;
