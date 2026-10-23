-- Probe 078: OPENJSON()
CREATE PROCEDURE dbo.probe_078_openjson
AS
BEGIN
    SELECT [key], value FROM OPENJSON('{"a":1}');
END
GO
