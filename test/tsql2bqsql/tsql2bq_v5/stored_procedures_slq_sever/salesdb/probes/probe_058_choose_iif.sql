-- Probe 058: CHOOSE() / IIF()
CREATE PROCEDURE dbo.probe_058_choose_iif
AS
BEGIN
    SELECT CHOOSE(2, 'a', 'b') AS c, IIF(Amount > 1, 'h', 'l') AS i FROM dbo.T;
END
GO
