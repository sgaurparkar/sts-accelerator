-- Probe 013: THROW <number>, <msg>, <state>
CREATE PROCEDURE dbo.probe_013_throw_number
AS
BEGIN
    THROW 50001, 'custom error', 1;
END
GO
