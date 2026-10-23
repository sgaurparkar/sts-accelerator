-- Probe 004: IDENTITY(1,1) column
CREATE PROCEDURE dbo.probe_004_identity_column
AS
BEGIN
    CREATE TABLE #x (Id INT IDENTITY(1,1), Name VARCHAR(10));
    INSERT INTO #x (Name) VALUES ('a');
    SELECT * FROM #x;
END
GO
