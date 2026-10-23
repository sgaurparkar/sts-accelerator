-- Probe 051: AT TIME ZONE
CREATE PROCEDURE dbo.probe_051_at_time_zone
AS
BEGIN
    SELECT CreatedAt AT TIME ZONE 'UTC' AS t FROM dbo.T;
END
GO
