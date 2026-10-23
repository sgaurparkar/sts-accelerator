-- Probe 081: Four-part name (linked server)
CREATE PROCEDURE dbo.probe_081_four_part
AS
BEGIN
    SELECT * FROM LinkedSrv.OtherDb.dbo.T;
END
GO
