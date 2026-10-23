-- Probe 002: @@IDENTITY
CREATE PROCEDURE dbo.probe_002_at_identity
AS
BEGIN
    DECLARE @id INT = @@IDENTITY;
    SELECT @id AS i;
END
GO
