-- Source: dbo.probe_077_for_json (SQL Server stored procedure)
-- Probe 077: FOR JSON PATH
CREATE OR REPLACE PROCEDURE `my-project.sql_server_centralasia_salesdb.probe_077_for_json`()
BEGIN
  -- NOTE: FOR JSON PATH -> TO_JSON_STRING(ARRAY(SELECT AS STRUCT ...)) - NULL columns appear as "col":null in BigQuery but are omitted by FOR JSON (add INCLUDE_NULL_VALUES semantics or strip them if consumers care)
  SELECT
    NULLIF(
      TO_JSON_STRING(
        ARRAY(SELECT AS STRUCT
          Id,
          Name
        FROM `my-project.sql_server_centralasia_salesdb.T`)
      ),
      '[]'
    ) AS json;
END;
