-- Probe 040: STRING_AGG()
CREATE PROCEDURE dbo.probe_040_string_agg
AS
BEGIN
    SELECT STRING_AGG(Name, ',') AS s FROM dbo.T;
END
GO
