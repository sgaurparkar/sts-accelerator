-- Source: dbo.probe_111_default_params (SQL Server stored procedure)
-- Probe 111: Default parameter values
-- Parameter defaults are not supported by BigQuery; callers must pass every argument:
--   @a = 5, @b = 'x'
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_111_default_params`(
  IN p_a INT64,
  IN p_b STRING
)
BEGIN
  SELECT
    p_a AS a,
    p_b AS b;
END;
