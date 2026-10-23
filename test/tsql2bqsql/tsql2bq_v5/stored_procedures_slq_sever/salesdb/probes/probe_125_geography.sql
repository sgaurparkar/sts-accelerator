-- Probe 125: GEOGRAPHY::Point / STAsText
CREATE PROCEDURE dbo.probe_125_geography
AS
BEGIN
    DECLARE @g GEOGRAPHY = GEOGRAPHY::Point(1, 2, 4326);
    SELECT @g.STAsText() AS t;
END
GO
