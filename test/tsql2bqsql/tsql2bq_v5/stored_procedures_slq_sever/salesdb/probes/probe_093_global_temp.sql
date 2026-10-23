-- Probe 093: ##global temp table
CREATE PROCEDURE dbo.probe_093_global_temp
AS
BEGIN
    CREATE TABLE ##g (Id INT);
    INSERT INTO ##g VALUES (1);
    SELECT * FROM ##g;
END
GO
