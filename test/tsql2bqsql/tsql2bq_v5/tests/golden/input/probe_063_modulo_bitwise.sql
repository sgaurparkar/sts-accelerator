-- Probe 063: % and & | ^ operators
CREATE PROCEDURE dbo.probe_063_modulo_bitwise
AS
BEGIN
    SELECT 7 % 3 AS m, 6 & 3 AS a, 6 | 1 AS o, 6 ^ 3 AS x;
END
GO
