-- Probe 084: INSERT ... EXEC proc
CREATE PROCEDURE dbo.probe_084_insert_exec
AS
BEGIN
    CREATE TABLE #r (a INT);
    INSERT INTO #r EXEC dbo.usp_Other;
END
GO
