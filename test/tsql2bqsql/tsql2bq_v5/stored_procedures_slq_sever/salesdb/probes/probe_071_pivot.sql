-- Probe 071: PIVOT
CREATE PROCEDURE dbo.probe_071_pivot
AS
BEGIN
    SELECT * FROM (SELECT Name, Amount FROM dbo.T) AS s PIVOT (SUM(Amount) FOR Name IN ([a], [b])) AS p;
END
GO
