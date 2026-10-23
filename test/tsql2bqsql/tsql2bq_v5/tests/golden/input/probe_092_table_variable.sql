-- Probe 092: DECLARE @t TABLE
CREATE PROCEDURE dbo.probe_092_table_variable
AS
BEGIN
    DECLARE @t TABLE (Id INT, Name VARCHAR(10));
    INSERT INTO @t SELECT Id, Name FROM dbo.T;
    SELECT * FROM @t;
END
GO
