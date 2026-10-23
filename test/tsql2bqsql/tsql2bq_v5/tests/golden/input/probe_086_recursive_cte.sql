-- Probe 086: Recursive CTE
CREATE PROCEDURE dbo.probe_086_recursive_cte
AS
BEGIN
    WITH n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < 5) SELECT i FROM n;
END
GO
