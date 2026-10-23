-- Probe 068: CROSS APPLY
CREATE PROCEDURE dbo.probe_068_cross_apply
AS
BEGIN
    SELECT c.Id, x.Total FROM dbo.T c CROSS APPLY (SELECT SUM(Amount) AS Total FROM dbo.T2 o WHERE o.Id = c.Id) x;
END
GO
