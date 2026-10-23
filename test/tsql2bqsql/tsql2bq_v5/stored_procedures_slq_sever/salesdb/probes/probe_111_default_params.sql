-- Probe 111: Default parameter values
CREATE PROCEDURE dbo.probe_111_default_params @a INT = 5, @b VARCHAR(10) = 'x' AS BEGIN SELECT @a AS a, @b AS b; END
GO
