-- Probe 096: UPDATE TOP (n)
CREATE PROCEDURE dbo.probe_096_update_top
AS
BEGIN
    UPDATE TOP (10) dbo.T SET Name = 'x';
END
GO
