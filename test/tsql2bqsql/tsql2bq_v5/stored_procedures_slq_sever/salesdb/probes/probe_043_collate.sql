-- Probe 043: COLLATE (case-sensitive collation)
CREATE PROCEDURE dbo.probe_043_collate
AS
BEGIN
    SELECT Id FROM dbo.T WHERE Name = 'a' COLLATE Latin1_General_CS_AS;
END
GO
