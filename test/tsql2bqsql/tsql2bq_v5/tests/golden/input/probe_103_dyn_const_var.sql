-- Probe 103: Constant dynamic SQL stored in a variable
CREATE PROCEDURE dbo.probe_103_dyn_const_var
AS
BEGIN
    DECLARE @sql NVARCHAR(MAX);
    SET @sql = N'UPDATE dbo.T SET LastRun = GETDATE() WHERE Id = @id';
    EXEC sp_executesql @sql, N'@id INT', @id = 1;
END
GO
