-- Probe 102: EXEC(@sql) with concatenated string
CREATE PROCEDURE dbo.probe_102_exec_concat
AS
BEGIN
    DECLARE @n NVARCHAR(50) = 'T';
    DECLARE @sql NVARCHAR(MAX) = N'SELECT * FROM dbo.' + @n;
    EXEC (@sql);
END
GO
