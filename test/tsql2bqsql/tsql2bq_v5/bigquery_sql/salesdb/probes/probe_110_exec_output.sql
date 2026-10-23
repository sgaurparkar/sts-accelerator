-- Source: dbo.probe_110_exec_output (SQL Server stored procedure)
-- Probe 110: EXEC proc with OUTPUT parameter
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_110_exec_output`()
BEGIN
  DECLARE v_r INT64;
  -- TODO(review): named arguments in call to usp_Other: callee not found, check argument order
  CALL `resource1.sql_server_centralasia_salesdb.usp_Other`(1  /* in */, v_r  /* out */);
  SELECT
    v_r AS r;
END;
