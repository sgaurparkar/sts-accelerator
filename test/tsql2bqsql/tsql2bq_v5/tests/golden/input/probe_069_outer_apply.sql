-- Probe 069: OUTER APPLY
CREATE PROCEDURE dbo.probe_069_outer_apply
AS
BEGIN
    SELECT c.Id, x.Total FROM dbo.T c OUTER APPLY (SELECT SUM(Amount) AS Total FROM dbo.T2 o WHERE o.Id = c.Id) x;
END
GO
