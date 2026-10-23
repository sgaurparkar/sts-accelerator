-- Probe 039: FORMAT(date, 'yyyy-MM-dd')
CREATE PROCEDURE dbo.probe_039_format_fn
AS
BEGIN
    SELECT FORMAT(CreatedAt, 'yyyy-MM-dd') AS f FROM dbo.T;
END
GO
