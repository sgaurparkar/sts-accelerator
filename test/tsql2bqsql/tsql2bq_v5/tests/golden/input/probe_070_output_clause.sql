-- Probe 070: OUTPUT clause (UPDATE ... OUTPUT)
CREATE PROCEDURE dbo.probe_070_output_clause
AS
BEGIN
    CREATE TABLE #chg (Id INT);
    UPDATE dbo.T SET Name = 'x' OUTPUT inserted.Id INTO #chg(Id) WHERE Id = 1;
END
GO
