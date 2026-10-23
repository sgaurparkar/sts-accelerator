-- Probe 087: MERGE (basic)
CREATE PROCEDURE dbo.probe_087_merge_basic
AS
BEGIN
    MERGE dbo.T AS t USING dbo.T2 AS s ON t.Id = s.Id WHEN MATCHED THEN UPDATE SET Name = s.Name WHEN NOT MATCHED THEN INSERT (Id, Name) VALUES (s.Id, s.Name);
END
GO
