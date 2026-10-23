-- Probe 080: OPENQUERY / linked server
CREATE PROCEDURE dbo.probe_080_openquery
AS
BEGIN
    SELECT * FROM OPENQUERY(LinkedSrv, 'SELECT 1 AS a');
END
GO
