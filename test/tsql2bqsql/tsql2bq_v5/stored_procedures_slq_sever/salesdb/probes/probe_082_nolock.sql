-- Probe 082: WITH (NOLOCK) table hint
CREATE PROCEDURE dbo.probe_082_nolock
AS
BEGIN
    SELECT Id FROM dbo.T WITH (NOLOCK) WHERE Id = 1;
END
GO
