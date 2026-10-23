-- Probe 129: #temp table
CREATE PROCEDURE dbo.probe_129_temp_table
AS
BEGIN
    CREATE TABLE #t (Id INT, Name VARCHAR(10));
    INSERT INTO #t VALUES (1, 'a');
    SELECT * FROM #t;
    DROP TABLE #t;
END
GO
