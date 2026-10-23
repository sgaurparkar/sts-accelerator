-- Probe 075: STUFF((... FOR XML PATH('')), 1, 1, '')
CREATE PROCEDURE dbo.probe_075_for_xml_path
AS
BEGIN
    DECLARE @s NVARCHAR(MAX);
    SELECT @s = STUFF((SELECT ',' + Name FROM dbo.T FOR XML PATH('')), 1, 1, '');
END
GO
