-- Probe 088: MERGE ... OUTPUT $action
CREATE PROCEDURE dbo.probe_088_merge_output
AS
BEGIN
    MERGE dbo.T AS t USING (SELECT 1 AS Id) AS s ON t.Id = s.Id WHEN MATCHED THEN UPDATE SET Name = 'x' OUTPUT $action, inserted.Id;
END
GO
