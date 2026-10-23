-- Probe 046: Text equality (case-insensitive in SQL Server)
CREATE PROCEDURE dbo.probe_046_case_insensitive
AS
BEGIN
    SELECT Id FROM dbo.T WHERE Name = 'abc';
END
GO
