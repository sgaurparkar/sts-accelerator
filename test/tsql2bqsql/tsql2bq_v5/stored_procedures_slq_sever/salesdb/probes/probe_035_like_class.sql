-- Probe 035: LIKE '[a-c]%' character class
CREATE PROCEDURE dbo.probe_035_like_class
AS
BEGIN
    SELECT Id FROM dbo.T WHERE Name LIKE '[a-c]%';
END
GO
