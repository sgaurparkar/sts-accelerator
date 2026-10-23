-- Source: dbo.probe_058_choose_iif (SQL Server stored procedure)
-- Probe 058: CHOOSE() / IIF()
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_058_choose_iif`()
BEGIN
  SELECT
    CASE 2 WHEN 1 THEN 'a' WHEN 2 THEN 'b' END AS c,
    IF(Amount > 1, 'h', 'l') AS i
  FROM `my-project.sql_server_centralasia_salesdb.T`;
END;
