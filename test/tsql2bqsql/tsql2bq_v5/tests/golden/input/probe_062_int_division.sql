-- Probe 062: int / int
CREATE PROCEDURE dbo.probe_062_int_division
AS
BEGIN
    DECLARE @a INT = 5, @b INT = 2;
    SELECT @a / @b AS q;
END
GO
