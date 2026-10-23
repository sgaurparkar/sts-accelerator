-- Source: dbo.probe_124_rowversion (SQL Server stored procedure)
-- Probe 124: ROWVERSION / TIMESTAMP
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_124_rowversion`()
BEGIN
  -- TODO(review): column Ver: rowversion has no equivalent; mapped to BYTES
  CREATE OR REPLACE TEMP TABLE tmp_rv (
    Id INT64,
    Ver BYTES
  );
  SELECT
    *
  FROM tmp_rv;
END;
