-- Probe 030: PARSENAME()
CREATE PROCEDURE dbo.probe_030_parsename
AS
BEGIN
    SELECT PARSENAME('a.b.c', 1) AS p;
END
GO
