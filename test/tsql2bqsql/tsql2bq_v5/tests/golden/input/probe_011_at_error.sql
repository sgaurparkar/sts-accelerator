-- Probe 011: @@ERROR
CREATE PROCEDURE dbo.probe_011_at_error
AS
BEGIN
    UPDATE dbo.T SET Name = 'x' WHERE Id = 1;
    IF @@ERROR <> 0 SELECT 1 AS failed;
END
GO
