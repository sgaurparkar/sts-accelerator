-- Source: dbo.probe_081_four_part (SQL Server stored procedure)
-- Probe 081: Four-part name (linked server)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_081_four_part`()
BEGIN
  -- TODO(review): four-part name LinkedSrv.OtherDb.dbo.T: linked servers are not reachable from BigQuery (load the data or use a federated/external table)
  SELECT
    *
  FROM LinkedSrv.OtherDb.dbo.T;
END;
