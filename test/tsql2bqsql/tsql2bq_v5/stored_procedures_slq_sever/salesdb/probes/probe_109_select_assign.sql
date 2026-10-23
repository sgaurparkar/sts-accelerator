-- Probe 109: SELECT @var = col FROM ...
CREATE PROCEDURE dbo.probe_109_select_assign
AS
BEGIN
    DECLARE @n VARCHAR(50);
    SELECT @n = Name FROM dbo.T WHERE Id = 1;
    SELECT @n AS n;
END
GO
