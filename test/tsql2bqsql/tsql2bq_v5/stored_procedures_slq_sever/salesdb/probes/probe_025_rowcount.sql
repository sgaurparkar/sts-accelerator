-- Probe 025: @@ROWCOUNT
CREATE PROCEDURE dbo.probe_025_rowcount
AS
BEGIN
    UPDATE dbo.T SET Name = 'x';
    SELECT @@ROWCOUNT AS n;
END
GO
