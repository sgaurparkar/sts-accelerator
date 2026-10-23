-- Probe 095: DELETE t FROM ... JOIN
CREATE PROCEDURE dbo.probe_095_delete_join
AS
BEGIN
    DELETE t FROM dbo.T t INNER JOIN dbo.T2 u ON t.Id = u.Id;
END
GO
