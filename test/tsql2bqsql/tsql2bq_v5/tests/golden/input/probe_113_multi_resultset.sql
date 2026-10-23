-- Probe 113: Several SELECT result sets
CREATE PROCEDURE dbo.probe_113_multi_resultset
AS
BEGIN
    SELECT 1 AS a;
    SELECT 2 AS b;
END
GO
