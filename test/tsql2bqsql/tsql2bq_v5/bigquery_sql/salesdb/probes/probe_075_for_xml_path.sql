-- Source: dbo.probe_075_for_xml_path (SQL Server stored procedure)
-- Probe 075: STUFF((... FOR XML PATH('')), 1, 1, '')
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_075_for_xml_path`()
BEGIN
  DECLARE v_s STRING;
  -- NOTE: STUFF((SELECT sep + x ... FOR XML PATH('')), 1, n, '') converted to STRING_AGG (note: FOR XML also escaped &, <, > as &amp; &lt; &gt; - STRING_AGG does not)
  SET v_s = (SELECT STRING_AGG(Name, ',') FROM `resource1.sql_server_centralasia_salesdb.T`);
END;
