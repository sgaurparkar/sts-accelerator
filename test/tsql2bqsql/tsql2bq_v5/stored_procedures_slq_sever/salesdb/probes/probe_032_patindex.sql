-- Probe 032: PATINDEX()
CREATE PROCEDURE dbo.probe_032_patindex
AS
BEGIN
    SELECT PATINDEX('%b%', Name) AS p FROM dbo.T;
END
GO
