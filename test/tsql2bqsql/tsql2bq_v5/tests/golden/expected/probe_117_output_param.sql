-- Source: dbo.probe_117_output_param (SQL Server stored procedure)
-- Probe 117: OUTPUT parameter
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_117_output_param`(
  INOUT p_r INT64
)
BEGIN
  SET p_r = 1;
END;
