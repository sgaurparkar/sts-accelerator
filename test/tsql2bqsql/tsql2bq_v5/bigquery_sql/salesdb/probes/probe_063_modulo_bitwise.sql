-- Source: dbo.probe_063_modulo_bitwise (SQL Server stored procedure)
-- Probe 063: % and & | ^ operators
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_063_modulo_bitwise`()
BEGIN
  SELECT
    MOD(7, 3) AS m,
    6 & 3 AS a,
    6 | 1 AS o,
    6 ^ 3 AS x;
END;
