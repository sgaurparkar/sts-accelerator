-- Probe 089: GOTO / labels
CREATE PROCEDURE dbo.probe_089_goto
AS
BEGIN
    DECLARE @i INT = 0;
    lbl: SET @i = @i + 1;
    IF @i < 3 GOTO lbl;
    SELECT @i AS i;
END
GO
