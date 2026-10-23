-- Probe 054: CONVERT(VARCHAR, date, style)
CREATE PROCEDURE dbo.probe_054_convert_style
AS
BEGIN
    SELECT CONVERT(VARCHAR(10), CreatedAt, 120) AS a, CONVERT(VARCHAR(10), CreatedAt, 103) AS b FROM dbo.T;
END
GO
