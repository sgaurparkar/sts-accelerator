-- Probe 097: DELETE TOP (n)
CREATE PROCEDURE dbo.probe_097_delete_top
AS
BEGIN
    DELETE TOP (10) FROM dbo.T WHERE Id > 0;
END
GO
