-- Probe 037: REPLICATE() / SPACE()
CREATE PROCEDURE dbo.probe_037_replicate_space
AS
BEGIN
    SELECT REPLICATE('a', 3) AS r, SPACE(2) AS s;
END
GO
