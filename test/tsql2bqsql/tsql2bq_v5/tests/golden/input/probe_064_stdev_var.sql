-- Probe 064: STDEV() / VAR()
CREATE PROCEDURE dbo.probe_064_stdev_var
AS
BEGIN
    SELECT STDEV(Amount) AS s, VAR(Amount) AS v FROM dbo.T;
END
GO
