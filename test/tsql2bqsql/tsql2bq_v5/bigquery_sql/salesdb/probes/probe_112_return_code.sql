-- Source: dbo.probe_112_return_code (SQL Server stored procedure)
-- Probe 112: RETURN <number>
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_112_return_code`()
BEGIN
  -- NOTE: RETURN value is discarded (BigQuery procedures return no status code; set config 'return_status_mode': 'out_param' to keep it in an OUT parameter)
  RETURN;
END;
