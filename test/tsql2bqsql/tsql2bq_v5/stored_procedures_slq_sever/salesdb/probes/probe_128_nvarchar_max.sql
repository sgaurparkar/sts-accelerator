-- Probe 128: NVARCHAR(MAX)
CREATE PROCEDURE dbo.probe_128_nvarchar_max
AS
BEGIN
    DECLARE @s NVARCHAR(MAX) = N'x';
    SELECT @s AS s;
END
GO
