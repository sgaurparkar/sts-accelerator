-- Probe 047: YEAR() / MONTH() / DAY()
CREATE PROCEDURE dbo.probe_047_year_month_day
AS
BEGIN
    SELECT YEAR(CreatedAt) AS y, MONTH(CreatedAt) AS m, DAY(CreatedAt) AS d FROM dbo.T;
END
GO
