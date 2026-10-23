-- Probe 105: WHILE + BREAK / CONTINUE
CREATE PROCEDURE dbo.probe_105_while_break
AS
BEGIN
    DECLARE @i INT = 0;
    WHILE @i < 5 BEGIN SET @i = @i + 1;
    IF @i = 2 CONTINUE;
    IF @i = 4 BREAK;
    END SELECT @i AS i;
END
GO
