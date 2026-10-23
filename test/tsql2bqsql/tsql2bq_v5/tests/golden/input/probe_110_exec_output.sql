-- Probe 110: EXEC proc with OUTPUT parameter
CREATE PROCEDURE dbo.probe_110_exec_output
AS
BEGIN
    DECLARE @r INT;
    EXEC dbo.usp_Other @In = 1, @Out = @r OUTPUT;
    SELECT @r AS r;
END
GO
