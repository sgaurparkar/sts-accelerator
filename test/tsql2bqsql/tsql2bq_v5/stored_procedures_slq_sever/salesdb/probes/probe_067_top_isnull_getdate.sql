-- Probe 067: TOP n / ISNULL / GETDATE (baseline)
CREATE PROCEDURE dbo.probe_067_top_isnull_getdate
AS
BEGIN
    SELECT TOP 5 Id, ISNULL(Name, 'x') AS n, GETDATE() AS d FROM dbo.T ORDER BY Id;
END
GO
