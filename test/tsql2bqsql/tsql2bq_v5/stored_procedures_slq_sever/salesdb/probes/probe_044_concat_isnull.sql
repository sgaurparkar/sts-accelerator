-- Probe 044: 'a' + ISNULL(x,'')
CREATE PROCEDURE dbo.probe_044_concat_isnull
AS
BEGIN
    SELECT 'a' + ISNULL(Name, '') AS s FROM dbo.T;
END
GO
