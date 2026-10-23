-- Source: dbo.probe_089_goto (SQL Server stored procedure)
-- Probe 089: GOTO / labels
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_089_goto`()
BEGIN
  DECLARE v_i INT64;
  SET v_i = 0;
  -- TODO(review): label lbl: BigQuery has no GOTO/labels; restructure with IF / LOOP / BREAK
  -- TODO(manual): label (GOTO target) is not supported
  --   lbl:
  SET v_i = v_i + 1;
  IF v_i < 3 THEN
    -- TODO(review): GOTO is not supported; rewrite manually
    -- TODO(manual): GOTO is not supported in BigQuery
    --   GOTO lbl
    ASSERT TRUE;  -- no-op placeholder: branch had no executable statement
  END IF;
  SELECT
    v_i AS i;
END;
