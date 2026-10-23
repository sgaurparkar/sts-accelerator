-- Probe 114: Table-valued parameter (READONLY)
CREATE PROCEDURE dbo.probe_114_tvp @items dbo.ItemList READONLY AS BEGIN SELECT COUNT(*) AS n FROM @items; END
GO
