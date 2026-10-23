-- Probe 107: CASE expression
CREATE PROCEDURE dbo.probe_107_case_expr
AS
BEGIN
    SELECT CASE WHEN Amount > 1 THEN 'h' ELSE 'l' END AS c FROM dbo.T;
END
GO
