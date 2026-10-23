-- Probe 121: XML type
CREATE PROCEDURE dbo.probe_121_xml_type
AS
BEGIN
    DECLARE @x XML = '<a/>';
    SELECT @x AS x;
END
GO
