-- Probe 101: sp_executesql with parameters
CREATE PROCEDURE dbo.probe_101_sp_executesql
AS
BEGIN
    EXEC sp_executesql N'SELECT * FROM dbo.T WHERE Id = @id', N'@id INT', @id = 1;
END
GO
