-- Probe 079: JSON_VALUE()
CREATE PROCEDURE dbo.probe_079_json_value
AS
BEGIN
    SELECT JSON_VALUE('{"a":1}', '$.a') AS v;
END
GO
