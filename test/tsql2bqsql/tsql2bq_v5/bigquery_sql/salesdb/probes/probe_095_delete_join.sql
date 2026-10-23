-- Source: dbo.probe_095_delete_join (SQL Server stored procedure)
-- Probe 095: DELETE t FROM ... JOIN
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_095_delete_join`()
BEGIN
  DELETE FROM `resource1.sql_server_centralasia_salesdb.T` AS t
  WHERE
    EXISTS(
      SELECT
        1
      FROM `resource1.sql_server_centralasia_salesdb.T2` AS u
      WHERE
        t.Id = u.Id
    );
END;
