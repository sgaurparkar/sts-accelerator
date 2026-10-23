-- Probe 094: UPDATE ... FROM ... JOIN
CREATE PROCEDURE dbo.probe_094_update_join
AS
BEGIN
    UPDATE t SET t.Name = 'x' FROM dbo.T t INNER JOIN dbo.T2 u ON t.Id = u.Id;
END
GO
