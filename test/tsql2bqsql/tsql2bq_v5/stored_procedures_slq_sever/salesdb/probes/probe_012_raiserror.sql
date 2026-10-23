-- Probe 012: RAISERROR(...)
CREATE PROCEDURE dbo.probe_012_raiserror
AS
BEGIN
    RAISERROR('Bad value %d', 16, 1, 5);
END
GO
