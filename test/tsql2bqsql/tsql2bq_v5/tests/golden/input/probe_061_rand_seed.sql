-- Probe 061: RAND(seed)
CREATE PROCEDURE dbo.probe_061_rand_seed
AS
BEGIN
    SELECT RAND(42) AS r;
END
GO
