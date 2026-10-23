-- Probe 127: VARBINARY(MAX) / 0x literal
CREATE PROCEDURE dbo.probe_127_binary_types
AS
BEGIN
    DECLARE @b VARBINARY(MAX) = 0x0102;
    SELECT @b AS b;
END
GO
