-- Probe 033: STRING_SPLIT()
CREATE PROCEDURE dbo.probe_033_string_split
AS
BEGIN
    SELECT value FROM STRING_SPLIT('a,b,c', ',');
END
GO
