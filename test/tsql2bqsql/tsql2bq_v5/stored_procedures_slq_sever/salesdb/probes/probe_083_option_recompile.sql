-- Probe 083: OPTION (RECOMPILE) query hint
CREATE PROCEDURE dbo.probe_083_option_recompile
AS
BEGIN
    SELECT Id FROM dbo.T OPTION (RECOMPILE);
END
GO
