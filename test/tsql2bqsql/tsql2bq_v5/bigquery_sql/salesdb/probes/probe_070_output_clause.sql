-- Source: dbo.probe_070_output_clause (SQL Server stored procedure)
-- Probe 070: OUTPUT clause (UPDATE ... OUTPUT)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_070_output_clause`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_chg (
    Id INT64
  );
  -- TODO(review): OUTPUT: OUTPUT clause is not supported
  UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    Id = 1
  RETURNING inserted.Id;
END;
