-- Probe 049: DATEADD() / DATEDIFF()
CREATE PROCEDURE dbo.probe_049_dateadd_diff
AS
BEGIN
    SELECT DATEADD(DAY, 5, CreatedAt) AS a, DATEDIFF(DAY, CreatedAt, GETDATE()) AS b FROM dbo.T;
END
GO
