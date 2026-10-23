-- Probe 116: System procedure (sp_rename)
CREATE PROCEDURE dbo.probe_116_sp_rename
AS
BEGIN
    EXEC sp_rename 'dbo.T.Name', 'Name2', 'COLUMN';
END
GO
