-- Probe 076: XML .value() / .query() / .nodes()
CREATE PROCEDURE dbo.probe_076_xml_value
AS
BEGIN
    DECLARE @x XML = '<a><b>1</b></a>';
    SELECT @x.value('(/a/b)[1]', 'INT') AS v;
END
GO
