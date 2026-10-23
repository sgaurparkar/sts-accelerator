-- Source: dbo.probe_094_update_join (SQL Server stored procedure)
-- Probe 094: UPDATE ... FROM ... JOIN
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_094_update_join`()
BEGIN
  -- NOTE: UPDATE ... FROM: BigQuery raises an error if a target row matches more than one source row (T-SQL silently uses one of them) - make sure the joined source is unique on the join key (aggregate it first if needed)
  UPDATE `resource1.sql_server_centralasia_salesdb.T` AS t SET Name = 'x'
  FROM `resource1.sql_server_centralasia_salesdb.T2` AS u
  WHERE
    t.Id = u.Id;
END;
