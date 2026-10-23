-- Source: dbo.probe_105_while_break (SQL Server stored procedure)
-- Probe 105: WHILE + BREAK / CONTINUE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_105_while_break`()
BEGIN
  DECLARE v_i INT64;
  SET v_i = 0;
  WHILE v_i < 5 DO
    SET v_i = v_i + 1;
    IF v_i = 2 THEN
      CONTINUE;
    END IF;
    IF v_i = 4 THEN
      BREAK;
    END IF;
  END WHILE;
  SELECT
    v_i AS i;
END;
