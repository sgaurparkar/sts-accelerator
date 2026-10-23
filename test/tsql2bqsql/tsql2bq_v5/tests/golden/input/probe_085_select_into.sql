-- Probe 085: SELECT ... INTO #t
CREATE PROCEDURE dbo.probe_085_select_into
AS
BEGIN
    SELECT Id, Name INTO #copy FROM dbo.T;
    SELECT * FROM #copy;
END
GO
