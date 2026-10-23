-- Probe 091: Full-text CONTAINS()
CREATE PROCEDURE dbo.probe_091_fulltext
AS
BEGIN
    SELECT Id FROM dbo.T WHERE CONTAINS(Name, 'abc');
END
GO
