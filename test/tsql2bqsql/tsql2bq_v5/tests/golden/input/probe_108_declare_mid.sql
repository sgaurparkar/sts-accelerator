-- Probe 108: DECLARE in the middle of the block
CREATE PROCEDURE dbo.probe_108_declare_mid
AS
BEGIN
    SELECT 1 AS a;
    DECLARE @x INT = 5;
    SELECT @x AS x;
END
GO
