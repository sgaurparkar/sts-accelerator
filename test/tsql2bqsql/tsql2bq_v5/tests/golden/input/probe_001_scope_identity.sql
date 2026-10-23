-- Probe 001: SCOPE_IDENTITY()
CREATE PROCEDURE dbo.probe_001_scope_identity
AS
BEGIN
    INSERT INTO dbo.T (Name) VALUES ('a');
    DECLARE @id INT = SCOPE_IDENTITY();
    SELECT @id AS NewId;
END
GO
