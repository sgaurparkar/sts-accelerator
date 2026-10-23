-- Probe 028: STUFF()
CREATE PROCEDURE dbo.probe_028_stuff
AS
BEGIN
    SELECT STUFF('abcdef', 2, 3, 'X') AS s;
END
GO
