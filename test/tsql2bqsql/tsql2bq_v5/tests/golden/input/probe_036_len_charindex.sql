-- Probe 036: LEN() / CHARINDEX()
CREATE PROCEDURE dbo.probe_036_len_charindex
AS
BEGIN
    SELECT LEN(Name) AS l, CHARINDEX('a', Name) AS c FROM dbo.T;
END
GO
